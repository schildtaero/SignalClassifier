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
