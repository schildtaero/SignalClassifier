import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from signalclassifier import ml
from signalclassifier.topologies import list_topologies, save_topology, validate_topology
from signalclassifier.execution import JobDeadlineExceeded
from signalclassifier.execution import probe_resources


def inline_execution(jobs, config, progress=None, *, axis="topologies", **_):
    """Deterministic coordinator stub for tests that mock the ML runner itself.

    Real process/RNG/gate behavior is covered by execution and integration tests.
    """
    outcomes = []
    if progress:
        progress({"stage": "execution", "progress": 0.})
    for index, job in enumerate(jobs):
        deadline = job.get("deadline")
        def forward(update):
            if deadline is not None and ml.time.monotonic() >= deadline:
                raise JobDeadlineExceeded("test deadline")
            if progress:
                progress({**update, "progress": (index + update.get("progress", 0)) / len(jobs),
                          "job_index": index, "trial": job.get("trial"), "topology_id": job.get("topology_id")})
        outcome = {"id": job["id"], "index": index, "pid": 123, "device": "cpu", "elapsed_seconds": 0.}
        try:
            if deadline is not None and ml.time.monotonic() >= deadline:
                outcome["pid"] = None
                raise JobDeadlineExceeded("test deadline before start")
            report = ml.run_experiment(job["config"], job["output_dir"], forward, **job.get("kwargs", {}))
            path = Path(job["output_dir"]) / "execution-result.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report), encoding="utf-8")
            outcome.update(status="completed", report=report, report_path=str(path))
        except JobDeadlineExceeded as exc:
            outcome.update(status="timed_out", error=str(exc))
        except (ValueError, RuntimeError, OSError) as exc:
            outcome.update(status="failed", error=str(exc))
        outcomes.append(outcome)
        if progress:
            progress({"stage": "execution_job_complete", "progress": (index + 1) / len(jobs),
                      "execution_job_result": {key: value for key, value in outcome.items() if key != "report"}})
    return {"outcomes": outcomes, "execution": {"axis": axis, "resolved": {"max_workers": 1}, "process_start_method": "test_inline"}}


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
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "run_experiment", side_effect=fake_run), \
             mock.patch.object(ml, "execute_jobs", side_effect=inline_execution):
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
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "run_experiment", side_effect=fake_run), \
             mock.patch.object(ml, "execute_jobs", side_effect=inline_execution):
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
        real_execute = ml.execute_jobs
        resources = probe_resources(include_gpu=False)
        # Exercise two lightweight real processes even on two-CPU CI where
        # the normal UI plan deliberately reserves one CPU for reception.
        resources["logical_cpus"] = max(3, resources["logical_cpus"])
        def two_slots(jobs, config, progress=None, **kwargs):
            return real_execute(jobs, config, progress, _resources=resources, **kwargs)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "execute_jobs", side_effect=two_slots):
            progress = []
            config = {"topology_ids": ["feature-lda", "feature-mlp-16", "iq-cnn-16"], "samples_per_class": 12,
                      "segment_length": 1024, "snr_db": [10, 20], "jitter": [0], "epochs": 1, "device": "cpu", "learning_rates": [.001, .002],
                      "execution": {"mode": "parallel", "max_workers": 2, "cpu_threads": 1, "preprocessing_workers": 1}}
            report = ml.run_comparison(config, directory, progress.append)
            self.assertEqual(report["topology_ids"], config["topology_ids"])
            self.assertEqual(len(report["comparison"]), 3)
            self.assertEqual(report["execution"]["peak_active_workers"], 2)
            self.assertEqual(report["execution"]["process_start_method"], "spawn")
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
                self.assertTrue(entry["report"]["training_execution"]["process_isolated"])
                self.assertEqual(entry["report"]["training_execution"]["cpu_threads"], 1)
                self.assertEqual(entry["report"]["training_execution"]["cpu_benchmark_scope"], "managed_experiment_workers_quiescent")
                self.assertEqual(len(entry["validation_trials"]), 2)
                self.assertIsNotNone(entry["score"]["total"])
                saved = json.loads((Path(entry["model_path"]) / "report.json").read_text())
                self.assertEqual(saved["score"]["total"], entry["score"]["total"])
                self.assertIn("score_definition", saved)
            self.assertEqual(progress[-1]["progress"], 1.)
            fractions = [update["progress"] for update in progress]
            self.assertEqual(fractions, sorted(fractions))
            self.assertEqual({update["topology_id"] for update in progress if "topology_id" in update}, set(config["topology_ids"]))

    def test_preprocessing_threads_keep_seed_order_and_small_sets_serial(self):
        config = ml._validate_config({"segment_length": 256, "snr_db": [0, 20], "jitter": [0, .1]})
        topology = next(item for item in list_topologies() if item["id"] == "feature-lda")
        entries = [{"iq": ml.generate_signal("AM" if index % 2 == 0 else "FM", 256, seed=index),
                    "label": index % 2, "sample_rate": 256000, "group_id": str(index)} for index in range(64)]
        results, remaining_rng = [], []
        for workers in (1, 2):
            token = ml._EXECUTION_CONTEXT.set({"resolved_preprocessing_workers": workers})
            try:
                rng = np.random.default_rng(17)
                results.append(ml._dataset(entries, topology, config, rng))
                remaining_rng.append(rng.integers(0, 2 ** 31, 8))
                if workers == 2:
                    with mock.patch.object(ml, "ThreadPoolExecutor", side_effect=AssertionError("small inputs stay serial")):
                        ml._dataset(entries[:4], topology, config, np.random.default_rng(17))
            finally:
                ml._EXECUTION_CONTEXT.reset(token)
        np.testing.assert_array_equal(results[0][0], results[1][0])
        np.testing.assert_array_equal(results[0][1], results[1][1])
        np.testing.assert_array_equal(remaining_rng[0], remaining_rng[1])

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
    def test_minibatched_training_reuses_validation_and_transfers_losses_per_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            report = ml.run_experiment({"topology_id": "feature-mlp-16", "samples_per_class": 24, "epochs": 2,
                                        "segment_length": 1024, "batch_size": 4, "snr_db": [20], "jitter": [0], "device": "cpu"}, directory,
                                       _execution_context={"resolved_cpu_threads": 1, "resolved_preprocessing_workers": 1})
            actual = report["training_execution"]
            self.assertEqual(actual["training_batch_size"], 4)
            self.assertEqual(actual["validation_batch_size"], 4)
            self.assertEqual(actual["loss_host_transfers"], 2)
            self.assertEqual(actual["validation_tensor_transfers"], 1)
            self.assertEqual(actual["training_batches"], 28)
            self.assertEqual(actual["validation_batches"], 10)
            self.assertEqual(actual["cpu_threads"], 1)
            self.assertEqual(actual["checkpoint_device"], "cpu")
            self.assertGreater(actual["checkpoint_bytes"], 0)
            for name in ("device_transfer_seconds", "training_epoch_seconds", "validation_seconds", "checkpoint_seconds",
                         "cpu_benchmark_seconds", "benchmark_wait_seconds"):
                self.assertGreaterEqual(report["phase_timings"][name], 0)
            self.assertTrue(all(np.isfinite(point["training_loss"]) and np.isfinite(point["validation_loss"]) for point in report["history"]))
            self.assertGreaterEqual(report["metrics"]["training_wall_seconds"], report["metrics"]["training_seconds"])
            self.assertAlmostEqual(report["metrics"]["training_wall_seconds"] - report["metrics"]["managed_training_pause_seconds"], report["metrics"]["training_seconds"])
            ml.validate_model(report["model_path"])

    def test_cpu_hardware_metadata_uses_cached_gpu_probe_without_touching_cuda(self):
        fake_torch = mock.Mock()
        fake_torch.__spec__ = importlib.util.find_spec("torch")
        fake_torch.get_num_threads.return_value = 1
        fake_torch.cuda.is_available.side_effect = AssertionError("CPU metadata must not initialize CUDA")
        fake_torch.cuda.get_device_properties.side_effect = AssertionError("CPU metadata must use cached GPU properties")
        token = ml._EXECUTION_CONTEXT.set({"device": "cpu", "resource_snapshot": {"cuda_available": True, "gpu_name": "cached GPU", "gpu_memory_bytes": 8 * 1024**3}})
        try:
            with mock.patch.dict(ml.sys.modules, {"torch": fake_torch}):
                result = ml.hardware_profile()
            self.assertEqual(result["gpu"], "cached GPU")
            self.assertEqual(result["cuda_probe_scope"], "cached_scheduler_resource_snapshot")
            self.assertTrue(result["cuda_available"])
        finally:
            ml._EXECUTION_CONTEXT.reset(token)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
    def test_training_score_time_excludes_observed_managed_pauses(self):
        observed_wait = [0.]
        def progress(update):
            if "epoch" in update:
                started = ml.time.perf_counter()
                ml.time.sleep(.01)
                observed_wait[0] += ml.time.perf_counter() - started
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "get_wait_seconds", side_effect=lambda: observed_wait[0]):
            report = ml.run_experiment({"topology_id": "feature-mlp-16", "samples_per_class": 12, "epochs": 2,
                                        "segment_length": 512, "batch_size": 4, "snr_db": [20], "jitter": [0], "device": "cpu"}, directory, progress,
                                       _execution_context={"resolved_cpu_threads": 1, "resolved_preprocessing_workers": 1})
            timing = report["metrics"]
            self.assertGreaterEqual(timing["managed_training_pause_seconds"], .02)
            self.assertAlmostEqual(timing["training_wall_seconds"] - timing["training_seconds"], timing["managed_training_pause_seconds"])
            self.assertEqual(report["score"]["inputs"]["training_seconds"], timing["training_seconds"])

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


class AutomaticOptimizationTests(unittest.TestCase):
    def config(self, **updates):
        return {"samples_per_class": 12, "segment_length": 512, "snr_db": [10, 20], "jitter": [0, .1], "epochs": 1,
                "optimization": {"enabled": True, "max_trials": 12, "timeout_seconds": 0,
                                 "search_space": {"segment_lengths": [256, 512, 1024], "batch_sizes": [4], "epochs": [1]}}, **updates}

    def test_real_search_uses_validation_only_then_tests_saved_winner_once(self):
        c = self.config()
        c["execution"] = {"mode": "parallel", "max_workers": 2, "cpu_threads": 1, "preprocessing_workers": 1}
        normalized = ml._validate_config(c)
        planned = ml._planned_split_groups(normalized)
        final_started = False
        final_calls = 0
        original_generate, original_final = ml.generate_signal, ml._evaluate_optimized_test
        original_execute = ml.execute_jobs
        resources = probe_resources(include_gpu=False)
        resources["logical_cpus"] = max(3, resources["logical_cpus"])
        test_seeds = {int(group.rsplit("-", 1)[1]) for group in planned["test"]}
        observed_test_seeds = []
        def generate(label, length=4096, sample_rate=256000, seed=42):
            if seed in test_seeds:
                self.assertTrue(final_started, "test source must not even be generated during search")
                observed_test_seeds.append(seed)
            return original_generate(label, length, sample_rate, seed)
        def final(*args, **kwargs):
            nonlocal final_started, final_calls
            final_started = True
            final_calls += 1
            return original_final(*args, **kwargs)
        def two_trials(jobs, config, progress=None, **kwargs):
            self.assertTrue(all(job["kwargs"]["_evaluate_test"] is False for job in jobs))
            self.assertTrue(all(job["kwargs"]["_planned_groups"] == planned for job in jobs))
            return original_execute(jobs, config, progress, _resources=resources, **kwargs)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "generate_signal", side_effect=generate), \
             mock.patch.object(ml, "execute_jobs", side_effect=two_trials), \
             mock.patch.object(ml, "_evaluate_optimized_test", side_effect=final):
            report = ml.run_experiment(c, directory)
            study = report["optimization"]
            self.assertEqual(final_calls, 1)
            self.assertEqual(set(observed_test_seeds), test_seeds)
            self.assertEqual(len(observed_test_seeds), len(test_seeds))
            self.assertEqual(study["completed_trials"], 3, "LDA only searches the three meaningful lengths")
            self.assertEqual(study["stop_reason"], "search_space_exhausted")
            self.assertEqual(study["test_evaluations"], 1)
            self.assertEqual(report["execution"]["peak_active_workers"], 2)
            self.assertEqual(report["execution"]["axis"], "trials")
            self.assertIsNotNone(report["metrics"]["accuracy"])
            self.assertEqual(report["final_test_evaluations"], 1)
            for trial in study["trials"]:
                self.assertEqual(trial["report"]["evaluation_scope"], "validation_only")
                self.assertIsNone(trial["report"]["metrics"]["accuracy"])
                self.assertEqual(trial["report"]["trials"], [])
                self.assertEqual(trial["report"]["data_usage"]["test"]["segments"], 0)
                self.assertEqual(trial["report"]["split_groups"], planned)
                self.assertEqual({(point["snr_db"], point["jitter"]) for point in trial["report"]["validation_trials"]}, {(10, 0), (10, .1), (20, 0), (20, .1)})
            self.assertAlmostEqual(study["total_training_seconds"], sum(trial["metrics"]["training_seconds"] for trial in study["trials"]))
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["best_config"], study["best_config"])
            self.assertFalse((Path(directory) / "optimization.json.tmp").exists())
            self.assertTrue(ml.validate_model(report["model_path"])["test_evaluation_available"])
            saved = json.loads((Path(report["model_path"]) / "report.json").read_text())
            self.assertEqual(saved["optimization"]["test_evaluations"], 1)

    def test_synthetic_reference_is_shared_and_training_only_is_rejected(self):
        config = ml._validate_config(self.config())
        small = ml._synthetic_groups(config, 256, include_test=False, reference_length=1024)
        large = ml._synthetic_groups(config, 512, include_test=False, reference_length=1024)
        for split in ("train", "validation"):
            for first, second in zip(small[split], large[split]):
                np.testing.assert_array_equal(first["iq"], second["iq"][:256])
                self.assertEqual(first["group_id"], second["group_id"])
        self.assertEqual(small["test"], [])
        with self.assertRaisesRegex(ValueError, "Validierung"):
            ml.validate_experiment_config(self.config(evaluation_mode="training_only"))

    def test_recorded_search_keeps_common_anchors_and_never_reads_test_iq_early(self):
        from signalclassifier import sigmf
        with tempfile.TemporaryDirectory() as directory:
            entries = []
            for label in ("AM", "FM"):
                for group in range(3):
                    path = sigmf.write_recording(Path(directory) / f"{label}-{group}", ml.generate_signal(label, 2048, seed=group), 256000, 100e6)
                    entries.append({"path": str(path), "label": label, "verified": True, "group_id": f"{label}-{group}"})
            config = self.config(source="recordings", recordings=entries)
            normalized = ml._validate_config(config)
            test_ids = set(ml._planned_split_groups(normalized)["test"])
            paths = {entry["path"]: entry["group_id"] for entry in entries}
            final_started = False
            original_read, original_final = sigmf.read_recording, ml._evaluate_optimized_test
            final_reads = []
            def read(path, *args, **kwargs):
                if paths[str(path)] in test_ids:
                    self.assertTrue(final_started)
                    final_reads.append(str(path))
                return original_read(path, *args, **kwargs)
            def final(*args, **kwargs):
                nonlocal final_started
                final_started = True
                return original_final(*args, **kwargs)
            with mock.patch.object(sigmf, "read_recording", side_effect=read), mock.patch.object(ml, "_evaluate_optimized_test", side_effect=final):
                report = ml.run_experiment(config, Path(directory) / "search")
            self.assertEqual(len(final_reads), len(test_ids))
            usage = [trial["report"]["data_usage"] for trial in report["optimization"]["trials"]]
            for split in ("train", "validation"):
                self.assertEqual(len({row[split]["segments"] for row in usage}), 1)
                self.assertEqual(len({row[split]["groups"] for row in usage}), 1)
            self.assertEqual(report["reference_window_samples"], 1024)
            with mock.patch.object(sigmf, "read_recording", side_effect=AssertionError("metadata preflight only")):
                filtered = ml.dataset_readiness({**config, "optimization": {"enabled": True, "search_space": {"segment_lengths": [256, 4096]}}})
            self.assertTrue(filtered["ready"])
            self.assertEqual(filtered["optimization"]["search_space"]["segment_lengths"], [256])
            self.assertEqual(filtered["optimization"]["excluded_segment_lengths"][0]["segment_length"], 4096)

    def fake_report(self, out, config, objective=50):
        model = Path(out) / "candidate-1"
        model.mkdir(parents=True)
        validation = {"macro_f1": .7, "balanced_accuracy": .8}
        metrics = {"training_seconds": 2., "preprocessing_seconds": 1., "model_bytes": 100, "accuracy": None}
        candidate = {"candidate": 1, "validation": validation, "model_path": str(model), **metrics}
        return {"config": {**config}, "model_path": str(model), "validation": validation, "score": {"total": objective},
                "metrics": metrics, "candidates": [candidate], "selected_candidate": 1}

    def test_timeout_keeps_completed_trials_and_cancellation_is_distinct(self):
        config = ml._validate_config(self.config())
        config["optimization"]["timeout_seconds"] = 10.
        now, calls = [0.], []
        def run(c, out, progress=None, **kwargs):
            calls.append(c)
            if len(calls) == 2:
                now[0] = 11.
            progress({"progress": .5, "stage": "train", "epoch": 1, "epoch_count": 2,
                      "training_loss": .4, "validation_loss": .5, "training_elapsed_seconds": 2., "preprocessing_seconds": 1.})
            return self.fake_report(out, c)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml.time, "perf_counter", side_effect=lambda: now[0]), \
             mock.patch.object(ml.time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch.object(ml, "execute_jobs", side_effect=inline_execution), \
             mock.patch.object(ml, "run_experiment", side_effect=run), mock.patch.object(ml, "_evaluate_optimized_test", return_value=0.) as final:
            report = ml._run_optimization(config, directory)
            state = report["optimization"]
            self.assertEqual(state["stop_reason"], "timeout")
            self.assertEqual(state["completed_trials"], 1)
            self.assertEqual(state["timed_out_trials"], 1)
            self.assertEqual(state["skipped_trials"], 1)
            self.assertEqual(state["total_training_seconds"], 2.)
            self.assertEqual(state["best_trial"], 1)
            final.assert_called_once()
        class ExperimentCancelled(Exception):
            pass
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "execute_jobs", side_effect=inline_execution):
            def cancel(_):
                raise ExperimentCancelled()
            with self.assertRaises(ExperimentCancelled):
                ml._run_optimization(config, directory, cancel)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["status"], "interrupted")
            self.assertEqual(state["stop_reason"], "user_cancellation")
            self.assertIsNone(state["best_config"])
            self.assertEqual(state["test_evaluations"], 0)

    def test_all_failed_trials_leave_partial_report_without_fabricated_winner(self):
        config = ml._validate_config(self.config())
        config["optimization"]["timeout_seconds"] = 300.
        def run(_config, _out, progress=None, **_kwargs):
            progress({"progress": .5, "stage": "train", "message": "Trainingsdaten an das Modell übergeben"})
            raise ValueError("bad trial: incompatible input dimensions")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "run_experiment", side_effect=run), \
             mock.patch.object(ml, "execute_jobs", side_effect=inline_execution), \
             mock.patch.object(ml, "_evaluate_optimized_test") as final:
            with self.assertRaisesRegex(ValueError, "keinen abgeschlossenen") as failure:
                ml._run_optimization(config, directory)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["failed_trials"], 3)
            self.assertEqual(state["stop_reason"], "all_trials_failed")
            self.assertIsNone(state["best_config"])
            self.assertEqual(state["failure_summary"]["trial_counts"]["failed"], 3)
            self.assertEqual(state["failure_summary"]["trial_counts"]["timed_out"], 0)
            self.assertEqual(state["failure_summary"]["errors"], [
                {"status": "failed", "error": "bad trial: incompatible input dimensions", "trials": [1, 2, 3]}])
            self.assertIn("bad trial: incompatible input dimensions", str(failure.exception))
            self.assertNotIn("Zeitbudget erhöhen", str(failure.exception))
            self.assertNotIn("0 für kein Suchzeitlimit", str(failure.exception))
            self.assertEqual(state["failure_summary"]["report_path"], str(Path(directory) / "optimization.json"))
            for trial in state["trials"]:
                self.assertEqual(trial["last_stage"], "train")
                self.assertEqual(trial["last_message"], "Trainingsdaten an das Modell übergeben")
            final.assert_not_called()

    def test_timeout_without_completed_trial_exposes_budget_and_last_phase(self):
        config = ml._validate_config(self.config())
        config["optimization"]["timeout_seconds"] = 300.
        now = [0.]
        def run(_config, _out, progress=None, **_kwargs):
            progress({"progress": .1, "stage": "preprocess", "message": "Eingangssegmente vorbereiten"})
            now[0] = 301.
            progress({"progress": .2, "stage": "train"})
            self.fail("the cooperative deadline should stop the worker")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml.time, "perf_counter", side_effect=lambda: now[0]), \
             mock.patch.object(ml.time, "monotonic", side_effect=lambda: now[0]), \
             mock.patch.object(ml, "execute_jobs", side_effect=inline_execution), \
             mock.patch.object(ml, "run_experiment", side_effect=run), \
             mock.patch.object(ml, "_evaluate_optimized_test") as final:
            with self.assertRaisesRegex(ValueError, "keinen abgeschlossenen") as failure:
                ml._run_optimization(config, directory)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["stop_reason"], "timeout")
            self.assertEqual(state["completed_trials"], 0)
            self.assertEqual(state["timed_out_trials"], 1)
            self.assertEqual(state["skipped_trials"], 2)
            self.assertEqual(state["failed_trials"], 0)
            summary = state["failure_summary"]
            self.assertEqual(summary["timeout_seconds"], 300.)
            self.assertEqual(summary["trial_counts"]["timed_out"], 1)
            self.assertEqual(summary["trial_counts"]["skipped"], 2)
            self.assertEqual(summary["started_timed_out_trials"], 1)
            self.assertEqual(summary["not_started_trials"], 2)
            self.assertIn("300 s je Topologie", str(failure.exception))
            self.assertIn("test deadline", str(failure.exception))
            self.assertEqual(state["trials"][0]["last_stage"], "preprocess")
            self.assertEqual(state["trials"][0]["last_message"], "Eingangssegmente vorbereiten")
            self.assertEqual(state["test_evaluations"], 0)
            final.assert_not_called()

    def test_nested_timeouts_with_parent_pid_distinguish_unstarted_trials_without_changing_raw_status(self):
        config = ml._validate_config(self.config(topology_id="feature-mlp-16"))
        config["optimization"]["timeout_seconds"] = 300.
        def execute(jobs, _config, progress=None, **_kwargs):
            self.assertEqual(len(jobs), 12)
            outcomes = []
            progress({"trial": 1, "stage": "train", "message": "Begonnener Suchversuch"})
            for index, _job in enumerate(jobs):
                error = "Zeitbudget des Suchversuchs erreicht" if index == 0 else (
                    "Zeitbudget vor Start erreicht" if index % 2 else "Zeitbudget vor Jobstart erreicht")
                outcome = {"index": index, "status": "timed_out", "pid": 1498, "device": "cpu", "error": error}
                outcomes.append(outcome)
                progress({"stage": "execution_job_complete", "execution_job_result": outcome})
            return {"outcomes": outcomes, "execution": {"axis": "trials"}}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "execute_jobs", side_effect=execute), \
             mock.patch.object(ml, "_evaluate_optimized_test") as final:
            with self.assertRaisesRegex(ValueError, "keinen abgeschlossenen") as failure:
                ml._run_optimization(config, directory)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            summary = state["failure_summary"]
            self.assertEqual(state["stop_reason"], "timeout")
            self.assertEqual(state["timed_out_trials"], 12)
            self.assertEqual(state["skipped_trials"], 0)
            self.assertEqual(summary["trial_counts"]["timed_out"], 12)
            self.assertEqual(summary["trial_counts"]["skipped"], 0)
            self.assertEqual(summary["started_timed_out_trials"], 1)
            self.assertEqual(summary["not_started_trials"], 11)
            self.assertIn("1 nach Start mit Zeitabbruch, 11 nicht gestartet", str(failure.exception))
            self.assertEqual([trial["status"] for trial in state["trials"]], ["timed_out"] * 12)
            self.assertEqual([trial["pid"] for trial in state["trials"]], [1498] * 12)
            self.assertEqual(state["trials"][0]["last_stage"], "train")
            final.assert_not_called()

    def test_completion_path_failure_retries_final_report_and_tests_winner_once(self):
        config = ml._validate_config(self.config())
        config["optimization"]["max_trials"] = 1
        def execute(jobs, _config, progress=None, **_kwargs):
            self.assertEqual(len(jobs), 1)
            report = self.fake_report(jobs[0]["output_dir"], jobs[0]["config"])
            outcome = {"index": 0, "status": "completed", "report_path": str(Path(directory) / "not-visible-yet.json"),
                       "pid": 123, "device": "cpu", "elapsed_seconds": 1.}
            progress({"stage": "execution_job_complete", "progress": 1., "execution_job_result": outcome})
            # The completion event and final batch are separate handoffs. A
            # failed first read must not discard the report delivered later.
            interim = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(interim["trials"][0]["status"], "failed")
            return {"outcomes": [{**outcome, "report": report}], "execution": {"axis": "trials"}}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "execute_jobs", side_effect=execute), \
             mock.patch.object(ml, "_evaluate_optimized_test", return_value=0.) as final:
            report = ml._run_optimization(config, directory)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completed_trials"], 1)
            self.assertEqual(state["failed_trials"], 0)
            self.assertEqual(state["best_trial"], 1)
            self.assertNotIn("error", state["trials"][0])
            self.assertEqual(state["test_evaluations"], 1)
            self.assertEqual(report["selected_candidate"], 1)
            final.assert_called_once()

    def test_irrecoverable_trial_report_errors_are_saved_without_interrupting_search(self):
        config = ml._validate_config(self.config())
        def execute(jobs, _config, progress=None, **_kwargs):
            outcomes = []
            for index, job in enumerate(jobs):
                outcome = {"index": index, "status": "completed", "pid": 123, "device": "cpu",
                           "report_path": str(Path(job["output_dir"]) / "missing.json")}
                progress({"stage": "execution_job_complete", "execution_job_result": outcome})
                if index == 1:
                    # A permanently malformed deserialized report must also be
                    # a failed trial, not a coordinator interruption.
                    outcome["report"] = []
                elif index == 2:
                    outcome["report"] = self.fake_report(job["output_dir"], job["config"])
                    outcome["report"]["metrics"].pop("model_bytes")
                outcomes.append(outcome)
            return {"outcomes": outcomes, "execution": {"axis": "trials"}}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(ml, "execute_jobs", side_effect=execute), \
             mock.patch.object(ml, "_evaluate_optimized_test") as final:
            with self.assertRaisesRegex(ValueError, "keinen abgeschlossenen"):
                ml._run_optimization(config, directory)
            state = json.loads((Path(directory) / "optimization.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["stop_reason"], "all_trials_failed")
            self.assertEqual(state["failed_trials"], 3)
            self.assertIsNone(state["best_trial"])
            self.assertEqual(state["test_evaluations"], 0)
            self.assertIn("missing.json", state["trials"][0]["error"])
            self.assertIn("kein JSON-Objekt", state["trials"][1]["error"])
            self.assertIn("Modellgröße", state["trials"][2]["error"])
            self.assertNotIn("interruption_reason", state)
            final.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch optional")
    def test_multiple_topologies_optimize_and_select_score_objective(self):
        config = self.config(topology_ids=["feature-lda", "feature-mlp-16"], snr_db=[20], jitter=[0])
        config["optimization"].update(max_trials=2, objective="score")
        with tempfile.TemporaryDirectory() as directory:
            report = ml.run_comparison(config, directory)
            self.assertEqual(report["selection_objective"], "score")
            winner = max(report["comparison"], key=lambda entry: (entry["score"]["total"], -entry["metrics"]["model_bytes"]))
            self.assertEqual(report["selected_topology_id"], winner["topology_id"])
            self.assertEqual(report["optimization_summary"]["test_evaluations"], 2)
            for entry in report["comparison"]:
                self.assertEqual(entry["report"]["optimization"]["completed_trials"], 2)
                self.assertEqual(entry["report"]["optimization"]["test_evaluations"], 1)
                ml.validate_model(entry["model_path"])


if __name__ == "__main__":
    unittest.main()
