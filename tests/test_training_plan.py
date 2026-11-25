"""Explicit all-recording training preset through the real HTTP boundary."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from signalclassifier import ml, training_plan
from signalclassifier.app import create_app, validate_experiment
from signalclassifier.sigmf import SigMFWriter


class TrainingPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.rt = self.app.state.runtime
        self.rt.analysis.close()
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.ids = []
        for index, label in enumerate(("AM", "FM")):
            for variant in range(2):
                identifier = f"{label}-{variant}"
                extra = {"signalclassifier:label": label, "signalclassifier:label_verified": True,
                         "signalclassifier:group_id": "one-session",
                         "sc:origin_recording_id": f"fixed-{label}"}
                if not variant:
                    extra["sc:generation"] = {"campaign_id": "one-session", "payload_root_id": f"fixed-{label}",
                        "planned_split": "train", "label_facet": "modulation", "facets": {"modulation": label},
                        "reference_kind": "controlled_generator_reference"}
                writer = SigMFWriter(self.root / "recordings" / identifier, 256000, 433.92e6, extra=extra)
                writer.append(ml.generate_signal(label, 922 if index == variant == 0 else 2048,
                                                seed=index*20+variant))
                writer.close()
                self.ids.append(identifier)

    def payload(self):
        return {"source": "recordings", "recording_ids": self.ids, "topology_ids": ["feature-lda"],
                "allow_same_session": True, "respect_generation_splits": True,
                "segment_length": 1024, "segment_lengths": [1024, 2048],
                "batch_size": 8, "batch_sizes": [8, 16], "learning_rates": [.002, .004],
                "samples_per_class": 64, "epochs": 1, "snr_db": [20], "jitter": [0], "device": "cpu",
                "optimization": {"enabled": True}}

    def test_plan_repairs_short_mixed_fixed_origins_without_start_or_dropping_files(self):
        payload = self.payload()
        before = {path.name: path.read_bytes() for path in (self.root / "recordings").iterdir()}
        blocked = self.client.post("/api/experiments/validate", json=payload).json()
        self.assertFalse(blocked["ready"])
        with patch.object(self.rt.executor, "submit", side_effect=AssertionError("no training in preset")), \
             patch.object(self.rt.capture, "start", side_effect=AssertionError("no receiver in preset")), \
             patch.object(self.rt.generator, "start", side_effect=AssertionError("no TX in preset")):
            response = self.client.post("/api/experiments/training-plan", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        settings = result["settings"]
        self.assertEqual(settings["segment_lengths"], [512])
        self.assertEqual(settings["learning_rates"], [.002])
        self.assertEqual(settings["batch_sizes"], [8])
        self.assertEqual(settings["evaluation_mode"], "training_only")
        self.assertFalse(settings["optimization"]["enabled"])
        self.assertFalse(settings["respect_generation_splits"])
        self.assertTrue(settings["use_all_recordings"])
        self.assertEqual(result["summary"]["selected_recordings"], 4)
        self.assertTrue(result["readiness"]["ready"], result["readiness"]["errors"])
        self.assertTrue(result["readiness"]["sampling"]["all_recordings_represented"])
        self.assertEqual(self.client.post("/api/experiments/validate", json={**payload, **settings}).json()["ready"], True)
        self.assertEqual(self.rt.store.count("experiment"), 0)
        self.assertEqual(self.rt.store.count("model"), 0)
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.root / "recordings").iterdir()})

    def test_prepared_model_is_trainable_but_has_no_quality_ranking(self):
        payload = self.payload()
        prepared = self.client.post("/api/experiments/training-plan", json=payload).json()
        config = validate_experiment({**payload, **prepared["settings"]}, self.rt)
        report = ml.run_experiment(config, self.root / "prepared-model")
        self.assertFalse(report["evaluation_available"])
        self.assertIsNone(report["metrics"]["accuracy"])
        self.assertIsNone(report["score"]["total"])
        self.assertEqual(report["split_groups"]["validation"], [])
        self.assertEqual(report["split_groups"]["test"], [])
        self.assertTrue(report["recording_sampling"]["all_recordings_represented"])
        self.assertEqual(report["recording_sampling"]["recordings"], 4)
        self.assertEqual(report["label_evidence"], "mixed_generator_and_recording_annotations")
        model = ml.load_model_metadata(report["model_path"])
        self.assertTrue(model["use_all_recordings"])
        self.assertEqual(model["recording_sampling"], report["recording_sampling"])

    def test_unusable_file_is_not_silently_padded_or_removed(self):
        writer = SigMFWriter(self.root / "recordings" / "too-short", 256000, 433.92e6,
            extra={"signalclassifier:label": "AM", "signalclassifier:label_verified": True})
        writer.append(ml.generate_signal("AM", 200))
        writer.close()
        payload = {**self.payload(), "recording_ids": self.ids+["too-short"]}
        response = self.client.post("/api/experiments/training-plan", json=payload)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("mindestens 256", response.json()["detail"])
        self.assertTrue((self.root / "recordings" / "too-short.sigmf-data").is_file())
        self.assertEqual(self.rt.store.count("experiment"), 0)

    def test_preset_preserves_admission_guards_and_does_not_mutate_request(self):
        payload = self.payload()
        original = copy.deepcopy(payload)
        training_plan.requested_settings(payload)
        self.assertEqual(payload, original)
        for change in ({"source": "synthetic"}, {"segment_lengths": [True]}, {"learning_rates": [float("inf")]},
                       {"batch_sizes": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                training_plan.requested_settings({**payload, **change})
        for change, status in (({"recording_ids": self.ids+["not-found"]}, 404),
                               ({"topology_ids": ["not-found"]}, 400),
                               ({"recording_ids": [self.ids[0]]}, 400)):
            response = self.client.post("/api/experiments/training-plan", json={**payload, **change})
            self.assertEqual(response.status_code, status, response.text)


if __name__ == "__main__":
    unittest.main()
