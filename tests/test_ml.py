import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from signalclassifier import ml
from signalclassifier.topologies import list_topologies, save_topology, validate_topology


class ModulationTests(unittest.TestCase):
    def test_features_and_optional_priors_do_not_use_rf_frequency_for_prediction(self):
        for modulation in ("AM", "FM"):
            x = ml.generate_signal(modulation, seed=17)
            first = ml.classify(x, 256000, 100e6, use_priors=False)
            second = ml.classify(x, 256000, 125e6, use_priors=True)
            self.assertEqual(first["label"], modulation)
            self.assertEqual(first["probabilities"], second["probabilities"])
            self.assertEqual(second["steps"][-1]["stage"], "Optionales Vorwissen")

    def test_noise_and_silence_abstain(self):
        rng = np.random.default_rng(9)
        noise = rng.normal(size=4096) + 1j * rng.normal(size=4096)
        self.assertEqual(ml.classify(noise, 256000)["label"], "unbekannt")
        self.assertEqual(ml.classify(np.zeros(4096), 256000)["label"], "kein Signal")
        with mock.patch.object(ml, "_load_inference", side_effect=AssertionError("must not load weights")):
            result = ml.classify(np.zeros(4096), 256000, model_path="missing-model")
            self.assertIn("saved_model_inference", result["skipped_stages"])

    def test_baseline_roundtrip_independent_groups_and_measured_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            report = ml.run_experiment({"seed": 18, "samples_per_class": 24, "snr_db": [10, 20], "jitter": [0, .1]}, directory)
            self.assertGreaterEqual(report["metrics"]["balanced_accuracy"], .9)
            self.assertEqual(report["source"], "synthetic")
            self.assertIsNone(report["metrics"]["false_alarm_rate"])
            sets = [set(report["split_groups"][key]) for key in ("train", "validation", "test")]
            self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
            self.assertGreater(report["metrics"]["latency_ms_p50"], 0)
            self.assertGreater(report["metrics"]["model_bytes"], 0)
            self.assertEqual(report["selected_candidate"], 1)
            self.assertEqual(ml.validate_model(report["model_path"])["classes"], ["AM", "FM"])
            for modulation in ("AM", "FM"):
                result = ml.classify(ml.generate_signal(modulation, seed=291), 256000, model_path=report["model_path"])
                self.assertEqual(result["label"], modulation)
                ex = result["explanation"]
                margin = sum(x["contribution"] for x in ex["contributions"]) + ex["bias"]
                p = result["probabilities"]
                log_odds = np.log(max(p[ex["compared_classes"][0]], 1e-100) / max(p[ex["compared_classes"][1]], 1e-100))
                self.assertAlmostEqual(margin, log_odds, places=3)
            saved = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(saved["confusion_matrix"], report["confusion_matrix"])

    def test_topology_proposals_and_bounded_customization(self):
        catalog = list_topologies()
        self.assertTrue(25 <= len(catalog) <= 50)
        self.assertEqual(len({x["id"] for x in catalog}), len(catalog))
        self.assertTrue(all("keine empirische Rangfolge" in x["evaluation"] for x in catalog))
        with self.assertRaises(ValueError):
            validate_topology({"id": "../outside"})
        with self.assertRaises(ValueError):
            validate_topology({"id": "iq-cnn-32", "parameters": {"width": 100000}})
        with tempfile.TemporaryDirectory() as directory:
            custom = save_topology({"base_id": "iq-cnn-32", "id": "my-cnn", "parameters": {"width": 24}}, directory)
            self.assertEqual(custom["parameters"]["width"], 24)
            self.assertTrue((Path(directory) / "topologies/my-cnn.json").exists())

    def test_invalid_config_and_cooperative_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ml.run_experiment({"segment_length": 2 ** 28}, directory)
            def cancel(_):
                raise RuntimeError("cancelled")
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                ml.run_experiment({}, directory, cancel)
            self.assertFalse((Path(directory) / "report.json").exists())

    def test_augmented_capture_groups_do_not_leak_across_classes(self):
        from signalclassifier.sigmf import write_recording
        with tempfile.TemporaryDirectory() as directory:
            recordings = []
            for group in range(3):
                for label in ("AM", "FM", "POCSAG"):
                    # A third labelled fixture exercises generic class heads, not RF decoding.
                    x = ml.generate_signal("AM" if label == "AM" else "FM", 2048, sample_rate=48000, seed=group + 19)
                    path = write_recording(Path(directory) / f"{label}-{group}", x, 48000, 433e6)
                    recordings.append({"path": str(path), "label": label, "verified": True, "group_id": f"physical-{group}"})
            report = ml.run_experiment({"source": "recordings", "recordings": recordings, "segment_length": 1024,
                                        "snr_db": [30], "sample_rate": 256000}, Path(directory) / "run")
            self.assertEqual(report["classes"], ["AM", "FM", "POCSAG"])
            self.assertEqual(len(report["confusion_matrix"]), 3)
            self.assertEqual(report["metrics"]["timed_sample_rates"], [48000.])
            groups = report["split_groups"]
            self.assertEqual(len(set().union(*(set(x) for x in groups.values()))), 3)
            self.assertEqual(sum(map(len, groups.values())), 3)
            manifest = ml.validate_model(report["model_path"])
            self.assertEqual(manifest["classes"], report["classes"])

    def test_readiness_counts_sessions_and_never_loads_iq(self):
        from signalclassifier.sigmf import write_recording
        with tempfile.TemporaryDirectory() as directory:
            entries = []
            for label in ("AM", "FM"):
                for clip in range(2):
                    path = write_recording(Path(directory) / f"{label}-{clip}", ml.generate_signal(label, 2048), 256000, 100e6,
                                           extra={"signalclassifier:group_id": "same-physical-session"})
                    entries.append({"path": str(path), "label": label, "verified": True})
            config = {"source": "recordings", "recordings": entries, "segment_length": 1024}
            with mock.patch("signalclassifier.sigmf.read_recording", side_effect=AssertionError("preflight must not read IQ")):
                result = ml.dataset_readiness(config)
            self.assertFalse(result["ready"])
            self.assertEqual(result["evaluation_mode"], "independent")
            self.assertEqual(result["group_count"], 1)
            for cls in result["classes"]:
                self.assertEqual((cls["recordings"], cls["groups"], cls["segments"], cls["required_groups"]), (2, 1, 4, 3))
            self.assertTrue(any("Nur trainieren" in error for error in result["errors"]))
            self.assertNotIn("evaluation_mode", config, "preflight must preserve caller configuration")
            with self.assertRaisesRegex(ValueError, "unabhängige Aufnahmegruppen"):
                ml.run_experiment(config, Path(directory) / "strict")
            self.assertFalse((Path(directory) / "strict").exists())
            config["evaluation_mode"] = "training_only"
            self.assertTrue(ml.dataset_readiness(config)["ready"])

    def test_training_only_persists_unvalidated_models_without_quality_metrics(self):
        from signalclassifier.sigmf import write_recording
        with tempfile.TemporaryDirectory() as directory:
            entries = []
            for label in ("AM", "FM"):
                path = write_recording(Path(directory) / label, ml.generate_signal(label, 2048), 48000, 100e6)
                entries.append({"path": str(path), "label": label, "verified": True, "group_id": "one-session"})
            topologies = ["feature-lda"] + (["fb-lstm-16"] if importlib.util.find_spec("torch") else [])
            for topology in topologies:
                config = {"source": "recordings", "recordings": entries, "evaluation_mode": "training_only", "segment_length": 1024,
                          "snr_db": [20], "jitter": [0], "epochs": 1, "topology_id": topology, "device": "cpu"}
                with mock.patch.object(ml, "_metrics", side_effect=AssertionError("no apparent evaluation on training data")):
                    report = ml.run_experiment(config, Path(directory) / topology)
                self.assertFalse(report["evaluation_available"])
                self.assertEqual(report["validation_status"], "unvalidated")
                self.assertEqual(report["trials"], [])
                self.assertEqual(report["confusion_matrix"], [])
                self.assertEqual(report["split_groups"], {"train": ["one-session"], "validation": [], "test": []})
                for key in ("accuracy", "balanced_accuracy", "macro_f1", "brier_score", "ece", "accuracy_ci95", "recall"):
                    self.assertIsNone(report["metrics"][key], key)
                self.assertEqual(report["metrics"]["count"], 0)
                self.assertEqual(report["metrics"]["timing_scope"], "training_segments")
                self.assertGreater(report["metrics"]["latency_ms_p50"], 0)
                self.assertIsNone(report["candidates"][0]["validation"])
                self.assertTrue(all(epoch["validation_loss"] is None for epoch in report["history"]))
                self.assertFalse(ml.validate_model(report["model_path"])["evaluation_available"])
                prediction = ml.classify(ml.generate_signal("AM", 1024), 48000, model_path=report["model_path"])
                self.assertEqual(prediction["validation_status"], "unvalidated")

    def test_preflight_reports_short_and_missing_files_and_rejects_training_only_sweep(self):
        from signalclassifier.sigmf import write_recording
        with tempfile.TemporaryDirectory() as directory:
            path = write_recording(Path(directory) / "short", ml.generate_signal("AM", 256), 256000, 100e6)
            config = {"source": "recordings", "evaluation_mode": "training_only", "segment_length": 1024,
                      "recordings": [{"path": str(path), "label": "AM", "verified": True},
                                     {"path": str(Path(directory) / "missing.sigmf-meta"), "label": "FM", "verified": True}]}
            result = ml.dataset_readiness(config)
            self.assertFalse(result["ready"])
            self.assertTrue(any("kürzer" in x for x in result["errors"]))
            self.assertTrue(any("nicht lesbar" in x for x in result["errors"]))
            self.assertEqual([x["groups"] for x in result["classes"]], [0, 0])
            config = {"source": "synthetic", "evaluation_mode": "training_only", "segment_lengths": [1024, 2048]}
            self.assertFalse(ml.dataset_readiness(config)["ready"])
            with self.assertRaisesRegex(ValueError, "genau eine Kombination"):
                ml.run_experiment(config, Path(directory) / "sweep")

    def test_more_than_128_short_recordings_are_all_used_within_iq_budget(self):
        from signalclassifier.sigmf import write_recording
        with tempfile.TemporaryDirectory() as directory:
            entries = []
            for index in range(130):
                label = "AM" if index % 2 == 0 else "FM"
                path = write_recording(Path(directory) / f"clip-{index}", ml.generate_signal(label, 256, seed=index), 256000, 100e6)
                entries.append({"path": str(path), "label": label, "verified": True, "group_id": f"session-{index}"})
            config = {"source": "recordings", "recordings": entries, "segment_length": 256,
                      "snr_db": [20], "jitter": [0], "samples_per_class": 12}
            readiness = ml.dataset_readiness(config)
            self.assertTrue(readiness["ready"], readiness["errors"])
            self.assertEqual(readiness["group_count"], 130)
            self.assertEqual(sum(row["recordings"] for row in readiness["classes"]), 130)
            report = ml.run_experiment(config, Path(directory) / "run")
            used_groups = set().union(*(set(groups) for groups in report["split_groups"].values()))
            self.assertEqual(used_groups, {entry["group_id"] for entry in entries})

    def test_recording_limit_1000_is_shared_by_readiness_and_loader(self):
        entries = [{"label": "AM" if index % 2 == 0 else "FM", "verified": True} for index in range(1001)]
        config = {"source": "recordings", "recordings": entries}
        self.assertEqual(ml.MAX_RECORDINGS, 1000)
        with mock.patch.object(ml, "_recording_inventory", side_effect=AssertionError("oversized file list must be rejected before inspection")):
            readiness = ml.dataset_readiness(config)
            self.assertFalse(readiness["ready"])
            self.assertTrue(any("1000" in error for error in readiness["errors"]))
            with self.assertRaisesRegex(ValueError, "1000"):
                ml._recording_groups(config, 256)

    def test_recording_iq_budget_still_refuses_instead_of_dropping_files(self):
        entries = [{"label": "AM" if index % 2 == 0 else "FM", "verified": True, "group_id": f"session-{index}",
                    "path": f"/unused/clip-{index}.sigmf-meta"} for index in range(5)]
        config = ml._validate_config({"source": "recordings", "recordings": entries, "evaluation_mode": "training_only",
                                      "segment_length": 65536, "samples_per_class": 64})
        def inventory(entry):
            return {**entry, "metadata_path": entry["path"], "sample_count": 4_194_304, "sample_rate": 256000}
        # Represent 32 MiB blocks virtually so the budget check itself is tested
        # without allocating the 128 MiB retained by the production loader.
        virtual_iq = mock.MagicMock()
        virtual_iq.__len__.return_value = 4_194_304
        virtual_iq.__getitem__.return_value.copy.return_value = np.zeros(1, dtype=np.complex64)
        with mock.patch.object(ml, "_recording_inventory", side_effect=inventory):
            readiness = ml.dataset_readiness(config)
            self.assertFalse(readiness["ready"])
            self.assertTrue(any("128-MiB-IQ-Budget" in error for error in readiness["errors"]))
            with mock.patch("signalclassifier.sigmf.read_recording", return_value=(virtual_iq, {"global": {"core:sample_rate": 256000}})) as read:
                with self.assertRaisesRegex(ValueError, "128-MiB-IQ-Budget"):
                    ml._recording_groups(config, 65536)
                self.assertEqual(read.call_count, 4, "must reject before reading the fifth file, never silently omit it")

    def test_readiness_checks_split_segment_cap_from_metadata_without_iq_reads(self):
        entries = [{"label": "AM" if index % 2 == 0 else "FM", "verified": True,
                    "group_id": f"session-{index}", "path": f"/unused/clip-{index}.sigmf-meta"} for index in range(130)]
        def inventory(entry):
            return {**entry, "metadata_path": entry["path"], "sample_count": 16384, "sample_rate": 256000}
        config = {"source": "recordings", "recordings": entries, "segment_length": 256,
                  "samples_per_class": 64, "seed": 42}
        with mock.patch.object(ml, "_recording_inventory", side_effect=inventory), \
             mock.patch("signalclassifier.sigmf.read_recording", side_effect=AssertionError("preflight must not read IQ")):
            strict = ml.dataset_readiness(config)
            self.assertFalse(strict["ready"])
            self.assertTrue(any("Training: 4992 Segmente" in error and "4096" in error for error in strict["errors"]))
            self.assertTrue(any("Weniger Aufnahmen" in error and "größere Segmentlänge" in error for error in strict["errors"]))
            training = ml.dataset_readiness({**config, "evaluation_mode": "training_only"})
            self.assertFalse(training["ready"])
            self.assertTrue(any("Training: 8320 Segmente" in error for error in training["errors"]))
            boundary = ml.dataset_readiness({**config, "evaluation_mode": "training_only", "recordings": entries[:64]})
            self.assertTrue(boundary["ready"], boundary["errors"])

    def test_comparison_selects_validation_instead_of_test_accuracy(self):
        catalog = list_topologies()
        baseline = next(topology for topology in catalog if topology["id"] == "feature-lda")
        custom = {**baseline, "id": "feature-lda-copy", "name": "Alternative Baseline"}
        config = {"topology_ids": [baseline["id"], custom["id"]], "topologies": [baseline, custom]}
        def fake_run(c, out, progress=None):
            first = c["topology_id"] == "feature-lda"
            return {"model_path": str(Path(out) / "candidate-1"), "config": {}, "topology": c["topology"],
                    "evaluation_available": True, "metrics": {"accuracy": .99 if first else .2, "model_bytes": 100},
                    "candidates": [{"candidate": 1, "validation": {"macro_f1": .6 if first else .8}}],
                    "selected_candidate": 1, "classes": ["AM", "FM"], "confusion_matrix": []}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "run_experiment", side_effect=fake_run):
            report = ml.run_comparison(config, directory)
            self.assertEqual(report["selected_topology_id"], custom["id"])
            self.assertEqual(report["selected_comparison_index"], 1)
            self.assertEqual(report["metrics"]["accuracy"], .2)
            self.assertFalse(report["comparison_policy"]["selection_uses_test_metrics"])
            saved = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(len(saved["comparison"]), 2)

    def test_training_only_comparison_has_no_winner_or_quality_ranking(self):
        baseline = next(topology for topology in list_topologies() if topology["id"] == "feature-lda")
        alternative = {**baseline, "id": "feature-lda-alternative", "name": "Eigene Baseline"}
        config = {"evaluation_mode": "training_only", "samples_per_class": 12, "segment_length": 1024,
                  "topology_ids": [baseline["id"], alternative["id"]], "topologies": [baseline, alternative], "snr_db": [20]}
        with tempfile.TemporaryDirectory() as directory:
            report = ml.run_comparison(config, directory)
            self.assertIsNone(report["selected_topology_id"])
            self.assertIsNone(report["selected_comparison_index"])
            self.assertFalse(report["comparison_policy"]["quality_ranking_available"])
            self.assertEqual(report["model_path"], report["comparison"][0]["model_path"])
            self.assertEqual(len(report["comparison"]), 2)
            for entry in report["comparison"]:
                self.assertFalse(entry["evaluation_available"])
                self.assertIsNone(entry["validation"])
                self.assertIsNone(entry["metrics"]["accuracy"])
                self.assertFalse(ml.validate_model(entry["model_path"])["evaluation_available"])

    def test_public_comparison_validation_checks_optional_ranges_and_resolution(self):
        for bad in ({"learning_rates": [.2]}, {"augmentation": {"iq_phase_deg": 90}},
                    {"topology_ids": []}, {"topology_ids": ["feature-lda", "feature-lda"]},
                    {"topology_ids": ["feature-lda"], "topologies": []}):
            with self.assertRaises(ValueError, msg=str(bad)):
                ml.validate_experiment_config(bad)
        original = {"topology_ids": ["feature-lda"], "learning_rates": [.001]}
        normalized = ml.validate_experiment_config(original)
        self.assertNotIn("topologies", original)
        self.assertEqual(normalized["topologies"][0]["id"], "feature-lda")

    def test_score_formula_and_realtime_credit_cap(self):
        validation = {"balanced_accuracy": .9}
        grid = [{"balanced_accuracy": .9}, {"balanced_accuracy": .7}]
        metrics = {"inference_device": "cpu", "real_time_factor_p95": .2, "training_seconds": 300}
        score = ml.score_candidate(validation, grid, metrics, 2)
        self.assertAlmostEqual(score["total"], 72.4)
        self.assertEqual(score["component_points"]["accuracy"], 36.)
        self.assertAlmostEqual(score["component_points"]["robustness"], 18.9)
        self.assertEqual(score["component_points"]["realtime"], 15.)
        self.assertEqual(score["component_points"]["training"], 2.5)
        self.assertTrue(score["eligible_for_live"])
        faster = ml.score_candidate(validation, grid, {**metrics, "real_time_factor_p95": .1}, 2)
        self.assertEqual(score["component_points"]["realtime"], faster["component_points"]["realtime"])
        self.assertEqual(score["total"], faster["total"])
        self.assertEqual(sum(score["weights"].values()), 100)

    def test_scoring_prefers_quality_and_requires_validation_and_cpu_p95(self):
        metrics = {"inference_device": "cpu", "real_time_factor_p95": 1., "training_seconds": 0}
        good = ml.score_candidate({"balanced_accuracy": 1.}, [{"balanced_accuracy": 1.}], metrics)
        fast_weak = ml.score_candidate({"balanced_accuracy": .6}, [{"balanced_accuracy": .6}], {**metrics, "real_time_factor_p95": .01})
        self.assertGreater(good["total"], fast_weak["total"])
        slow = ml.score_candidate({"balanced_accuracy": 1.}, [{"balanced_accuracy": 1.}], {**metrics, "real_time_factor_p95": 1.01})
        self.assertFalse(slow["eligible_for_live"])
        self.assertIsNotNone(slow["total"], "quality score and runtime eligibility are separate")
        for candidate in (ml.score_candidate(None, [], metrics, evaluation_available=False),
                          ml.score_candidate({"balanced_accuracy": .9}, [], metrics),
                          ml.score_candidate({"balanced_accuracy": .9}, [{"balanced_accuracy": .9}], {"training_seconds": 1}),
                          ml.score_candidate({"balanced_accuracy": .9}, [{"balanced_accuracy": .9}], {**metrics, "inference_device": "cuda"})):
            self.assertIsNone(candidate["total"])
            self.assertFalse(candidate["quality_score_available"])
            self.assertTrue(candidate["reasons"])
        chance = ml.score_candidate({"balanced_accuracy": 1/3}, [{"balanced_accuracy": 1/3}], {**metrics, "real_time_factor_p95": .2}, 3)
        self.assertEqual(chance["component_points"]["accuracy"], 0.)
        self.assertEqual(chance["component_points"]["robustness"], 0.)

    def test_rankings_and_top_three_recommendations_use_validation_and_cpu_eligibility(self):
        baseline = next(topology for topology in list_topologies() if topology["id"] == "feature-lda")
        topologies = [{**baseline, "id": f"candidate-{i}", "name": f"Kandidat {i}"} for i in range(5)]
        qualities = [.9, .8, .99, .85, .7]
        rtfs = [.2, .1, 1.1, .2, .2]
        def fake_run(c, out, progress=None):
            index = int(c["topology_id"].split("-")[-1])
            validation = {"macro_f1": qualities[index], "balanced_accuracy": qualities[index]}
            trials = [{"snr_db": 10, "jitter": 0, "balanced_accuracy": qualities[index]}]
            return {"model_path": str(Path(out) / "candidate-1"), "config": {}, "topology": c["topology"],
                    "evaluation_available": True, "classes": ["AM", "FM"], "confusion_matrix": [],
                    "metrics": {"accuracy": 1 - qualities[index], "balanced_accuracy": 1 - qualities[index], "model_bytes": 100,
                                "inference_device": "cpu", "real_time_factor_p95": rtfs[index], "training_seconds": 0},
                    "candidates": [{"candidate": 1, "validation": validation, "validation_trials": trials}],
                    "selected_candidate": 1, "validation_trials": trials}
        config = {"topology_ids": [topology["id"] for topology in topologies], "topologies": topologies}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "run_experiment", side_effect=fake_run):
            report = ml.run_comparison(config, directory)
            self.assertEqual(report["rankings"]["accuracy"], ["candidate-2", "candidate-0", "candidate-3", "candidate-1", "candidate-4"])
            self.assertEqual(report["rankings"]["score"][0], "candidate-2")
            self.assertEqual(report["recommended_topology_ids"], ["candidate-0", "candidate-3", "candidate-1"])
            self.assertEqual(report["comparison"][2]["accuracy_rank"], 1)
            self.assertEqual(report["comparison"][2]["score_rank"], 1)
            self.assertFalse(report["comparison"][2]["score"]["eligible_for_live"])
            self.assertEqual(report["selected_topology_id"], "candidate-2", "legacy selection remains validation F1")
            qualities[:] = [.9] * 5
            rtfs[:] = [.2, .1, .05, .2, .1]
            tied = ml.run_comparison(config, Path(directory) / "clipped-tie")
            self.assertEqual(tied["rankings"]["score"], config["topology_ids"],
                             "speed above 5x must not break an otherwise equal ranking")

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
    def test_baseline_and_two_anns_share_data_and_keep_reloadable_models(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = []
            config = {"topology_ids": ["feature-lda", "feature-mlp-16", "iq-cnn-16"], "samples_per_class": 12,
                      "segment_length": 1024, "snr_db": [10, 20], "jitter": [0], "epochs": 1, "device": "cpu", "learning_rates": [.001, .002]}
            report = ml.run_comparison(config, directory, progress.append)
            self.assertEqual(report["topology_ids"], config["topology_ids"])
            self.assertEqual(len(report["comparison"]), 3)
            reports = [entry["report"] for entry in report["comparison"]]
            for candidate in reports[1:]:
                self.assertEqual(reports[0]["split_groups"], candidate["split_groups"])
                self.assertEqual(reports[0]["dataset_sha256"], candidate["dataset_sha256"])
            self.assertEqual(len({candidate["model_path"] for candidate in reports}), 3)
            for entry in report["comparison"]:
                self.assertTrue(ml.validate_model(entry["model_path"])["evaluation_available"])
                self.assertTrue((Path(entry["model_path"]).parent / "report.json").exists())
                self.assertEqual(entry["metrics"]["inference_device"], "cpu")
                self.assertGreaterEqual(entry["metrics"]["real_time_factor_p95"], entry["metrics"]["real_time_factor"])
                self.assertEqual(entry["report"]["hardware"]["inference_device"], "cpu")
                self.assertEqual(entry["report"]["hardware"]["training_device"], "cpu")
                self.assertEqual(len(entry["validation_trials"]), 2)
                self.assertIsNotNone(entry["score"]["total"])
                saved = json.loads((Path(entry["model_path"]) / "report.json").read_text())
                self.assertEqual(saved["score"]["total"], entry["score"]["total"])
                self.assertIn("score_definition", saved)
            self.assertEqual(progress[-1]["progress"], 1.)
            fractions = [update["progress"] for update in progress]
            self.assertEqual(fractions, sorted(fractions))
            self.assertEqual({update["topology_id"] for update in progress if "topology_id" in update}, set(config["topology_ids"]))

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
    def test_all_neural_families_forward_and_checkpoint(self):
        import torch
        torch.set_num_threads(2)
        tested = set()
        for topology in list_topologies():
            if topology["family"] in tested or topology["family"] == "feature_lda":
                continue
            tested.add(topology["family"])
            model = ml._torch_model(topology, 3)
            x = ml._representation(ml.generate_signal("AM", 1024), 256000, topology["input"])
            model.eval()
            out = model(torch.from_numpy(np.stack([x, x])))
            self.assertEqual(tuple(out.shape), (2, 3), topology["id"])
            self.assertTrue(torch.isfinite(out).all())
        with tempfile.TemporaryDirectory() as directory:
            report = ml.run_experiment({"topology_id": "fb-lstm-16", "samples_per_class": 12, "epochs": 1,
                                        "segment_length": 1024, "snr_db": [20], "device": "cpu"}, directory)
            ml.validate_model(report["model_path"])
            result = ml.classify(ml.generate_signal("FM", 1024), 256000, model_path=report["model_path"], explain=True)
            self.assertEqual(result["explanation"]["input_domain"], "feature_sequence")
            self.assertEqual(len(result["explanation"]["contributions"]), 16)


if __name__ == "__main__":
    unittest.main()
