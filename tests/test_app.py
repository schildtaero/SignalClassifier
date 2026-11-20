"""End-to-end API checks for the portable acquisition / training workflow."""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
import zipfile
import uuid
from unittest.mock import patch
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from signalclassifier.app import create_app
from signalclassifier.ml import generate_signal
from signalclassifier.sigmf import write_recording


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client_context = TestClient(create_app(self.root / "library"))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def import_fixture(self):
        meta_path = Path(write_recording(self.root / "fixture", generate_signal("AM", 4096), 256000, 125000000))
        with meta_path.open("rb") as meta, meta_path.with_suffix(".sigmf-data").open("rb") as data:
            response = self.client.post("/api/recordings/import", files={"metadata": (meta_path.name, meta, "application/json"), "data": ("fixture.sigmf-data", data, "application/octet-stream")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def import_modulation(self, modulation, frequency, seed=42):
        base = self.root / f"{modulation}-{frequency}-{seed}-{uuid.uuid4().hex}"
        path = Path(write_recording(base, generate_signal(modulation, 4096, seed=seed), 256000, frequency))
        response = self.client.post("/api/recordings/import", files={
            "metadata": (path.name, path.read_bytes(), "application/json"),
            "data": (path.with_suffix(".sigmf-data").name, path.with_suffix(".sigmf-data").read_bytes(), "application/octet-stream")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_for_experiment(self, identifier):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.client.get(f"/api/experiments/{identifier}").json()
            if state["status"] not in ("running", "queued", "cancelling"):
                return state
            time.sleep(.025)
        self.fail("Experiment did not reach a terminal state")

    def test_recording_labels_and_group_survive_export_and_reimport(self):
        recording = self.import_fixture()
        identifier = recording["id"]
        response = self.client.post(f"/api/recordings/{identifier}/label", json={"label": "AM", "verified": True, "notes": "Unabhängiger Test"})
        self.assertEqual(response.status_code, 200)
        downloaded = self.client.get(f"/api/recordings/{identifier}/download")
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            meta_name = next(x for x in archive.namelist() if x.endswith(".sigmf-meta"))
            data_name = next(x for x in archive.namelist() if x.endswith(".sigmf-data"))
            response = self.client.post("/api/recordings/import", files={"metadata": (meta_name, archive.read(meta_name), "application/json"), "data": (data_name, archive.read(data_name), "application/octet-stream")})
        self.assertEqual(response.status_code, 200, response.text)
        imported = response.json()
        self.assertTrue(imported["annotation"]["verified"])
        self.assertEqual(imported["annotation"]["label"], "AM")
        left = recording["metadata"]["global"]["signalclassifier:session_id"]
        right = imported["metadata"]["global"]["signalclassifier:session_id"]
        self.assertEqual(left, right)
        hint = self.client.get("/api/labels/suggest", params={"frequency_hz": 125000000}).json()
        self.assertTrue(hint["suggestions"])
        self.assertTrue(all(s["requires_confirmation"] for s in hint["suggestions"]))

    def test_experiment_model_export_import_and_classification(self):
        response = self.client.post("/api/experiments", json={"topology_id": "feature-lda", "samples_per_class": 30, "segment_length": 1024, "snr_db": [10, 20], "jitter": [0], "epochs": 1, "device": "cpu"})
        self.assertEqual(response.status_code, 200, response.text)
        state = self.wait_for_experiment(response.json()["id"])
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertGreater(state["metrics"]["balanced_accuracy"], .8)
        groups = state["report"]["split_groups"]
        self.assertFalse(set(groups["train"]) & set(groups["test"]))
        model = state["model_id"]
        archive = self.client.get(f"/api/models/{model}/download")
        imported = self.client.post("/api/models/import", files={"file": ("portable.zip", archive.content, "application/zip")})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertIsNotNone(imported.json()["score"]["total"])
        self.assertEqual(imported.json()["assessment_hardware"]["inference_device"], "cpu")
        selected = imported.json()["id"]
        self.assertEqual(self.client.post(f"/api/models/{selected}/activate").status_code, 200)
        recording = self.import_fixture()
        prediction = self.client.post(f"/api/recordings/{recording['id']}/classify", json={"model_id": selected})
        self.assertEqual(prediction.status_code, 200, prediction.text)
        self.assertEqual(prediction.json()["label"], "AM")
        self.assertTrue(prediction.json()["steps"])
        started = self.client.post("/api/receiver/start", json={"source": "synthetic", "synthetic_modulation": "AM", "mode": "monitor",
            "sample_rate": 256000, "center_frequency_hz": 125000000, "block_size": 4096, "duration_seconds": .5})
        self.assertEqual(started.status_code, 200, started.text)
        # Several decimated blocks must accumulate before a trained window is available.
        deadline = time.monotonic() + 3
        live = {}
        while time.monotonic() < deadline:
            live = self.client.get("/api/spectrum").json().get("classification") or {}
            if live.get("confidence_kind") == "uncalibrated_model_score":
                break
            time.sleep(.02)
        self.assertEqual(live.get("method"), "feature-lda", live)
        self.assertEqual(live.get("confidence_kind"), "uncalibrated_model_score", live)

    def test_reject_arbitrary_paths_and_zip_traversal(self):
        response = self.client.post("/api/receiver/start", json={"source": "recording", "recording_id": "../secret", "recording_path": "/etc/passwd"})
        self.assertEqual(response.status_code, 400)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../escape.txt", "no")
        response = self.client.post("/api/models/import", files={"file": ("bad.zip", stream.getvalue(), "application/zip")})
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.root / "escape.txt").exists())

    def test_idle_default_hardware_limits_and_browser_origin(self):
        status = self.client.get("/api/status").json()
        self.assertFalse(status["receiver"]["running"])
        self.assertIsNone(status["receiver"]["source"])
        response = self.client.post("/api/receiver/start", json={"source": "pluto", "center_frequency_hz": 125000000})
        self.assertEqual(response.status_code, 400)
        self.assertIn("325", response.json()["detail"])
        blocked = self.client.post("/api/receiver/stop", headers={"Origin": "https://unrelated.invalid"})
        self.assertEqual(blocked.status_code, 403)

    def test_frequency_proposals_batch_confirmation_and_preserved_groups(self):
        am = self.import_modulation("AM", 125e6)
        fm = self.import_modulation("FM", 100e6)
        ambiguous = self.import_modulation("FM", 433.92e6)
        self.assertIsNone(am["annotation"])
        self.assertEqual(am["label_suggestion"]["label"], "AM")
        self.assertEqual(fm["label_suggestion"]["label"], "FM")
        self.assertIsNone(ambiguous["label_suggestion"])
        self.assertEqual(self.client.get("/api/labels").json(), [])
        changed = self.client.post("/api/recordings/labels/batch", json={"recording_ids": [am["id"]], "expected_labels": {am["id"]: "FM"}})
        self.assertEqual(changed.json()["count"], 0)
        self.assertIn("geändert", changed.json()["skipped"][0]["reason"])
        response = self.client.post("/api/recordings/labels/batch", json={"recording_ids": [am["id"], fm["id"], ambiguous["id"]], "use_suggestions": True, "verified": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(response.json()["skipped"][0]["id"], ambiguous["id"])
        saved = {r["id"]: r for r in self.client.get("/api/recordings").json()}
        for original in (am, fm):
            current = saved[original["id"]]
            self.assertTrue(current["annotation"]["verified"])
            self.assertEqual(current["annotation"]["provenance"], "human-confirmed-frequency-prior")
            self.assertEqual(current["metadata"]["global"]["signalclassifier:session_id"], original["metadata"]["global"]["signalclassifier:session_id"])
            self.assertEqual(current["metadata"]["global"]["signalclassifier:label"], original["label_suggestion"]["label"])
        # Confirmed labels are retained by default, even when a conflicting common label is requested.
        kept = self.client.post("/api/recordings/labels/batch", json={"recording_ids": [am["id"]], "use_suggestions": False, "label": "FM"})
        self.assertEqual(kept.json()["count"], 0)
        self.assertEqual({row["id"]: row for row in self.client.get("/api/labels").json()}[am["id"]]["label"], "AM")

    def test_batch_unknown_id_and_database_failure_leave_metadata_unchanged(self):
        am = self.import_modulation("AM", 125e6)
        fm = self.import_modulation("FM", 100e6)
        runtime = self.client.app.state.runtime
        originals = {r["id"]: Path(r["metadata_path"]).read_bytes() for r in (am, fm)}
        missing = self.client.post("/api/recordings/labels/batch", json={"recording_ids": [am["id"], "missing-recording"]})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.get("/api/labels").json(), [])
        with patch.object(runtime.store, "put_many", side_effect=OSError("Simulierter Datenträgerfehler")):
            response = self.client.post("/api/recordings/labels/batch", json={"recording_ids": [am["id"], fm["id"]]})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.client.get("/api/labels").json(), [])
        for recording in (am, fm):
            self.assertEqual(Path(recording["metadata_path"]).read_bytes(), originals[recording["id"]])
        self.assertFalse(list((self.root / "library" / "recordings").glob(".*.label-*")))

    def test_dataset_preflight_counts_sessions_and_training_only_is_unvalidated(self):
        recordings = [self.import_modulation(mod, freq, seed=77) for mod, freq in (("AM", 125e6), ("FM", 100e6)) for _ in range(3)]
        identifiers = [recording["id"] for recording in recordings]
        confirmed = self.client.post("/api/recordings/labels/batch", json={"recording_ids": identifiers})
        self.assertEqual(confirmed.json()["count"], 6)
        config = {"source": "recordings", "recording_ids": identifiers, "topology_id": "feature-lda", "segment_length": 1024, "snr_db": [20], "jitter": [0], "epochs": 1}
        readiness = self.client.post("/api/experiments/validate", json=config).json()
        self.assertFalse(readiness["ready"])
        self.assertEqual({c["label"]: c["groups"] for c in readiness["classes"]}, {"AM": 1, "FM": 1})
        self.assertTrue(any("Nur trainieren" in message for message in readiness["errors"]))
        self.assertEqual(self.client.post("/api/experiments", json=config).status_code, 400)
        self.assertEqual(self.client.get("/api/experiments").json(), [])
        config["evaluation_mode"] = "training_only"
        self.assertTrue(self.client.post("/api/experiments/validate", json=config).json()["ready"])
        response = self.client.post("/api/experiments", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        state = self.wait_for_experiment(response.json()["id"])
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertFalse(state["report"]["evaluation_available"])
        self.assertIsNone(state["metrics"]["accuracy"])
        self.assertEqual(state["report"]["confusion_matrix"], [])
        self.assertEqual(state["report"]["split_groups"]["test"], [])

    def test_multiple_topologies_persist_and_live_comparison_does_not_start_receiver(self):
        custom = self.client.post("/api/topologies", json={"base_id": "feature-lda", "id": "feature-lda-reference", "name": "Zweite Referenz"})
        self.assertEqual(custom.status_code, 200, custom.text)
        response = self.client.post("/api/experiments", json={"topology_ids": ["feature-lda", "feature-lda-reference"], "samples_per_class": 24,
            "segment_length": 1024, "snr_db": [20], "jitter": [0], "epochs": 1})
        self.assertEqual(response.status_code, 200, response.text)
        experiment = self.wait_for_experiment(response.json()["id"])
        self.assertEqual(experiment["status"], "completed", experiment.get("error"))
        self.assertEqual(len(experiment["model_ids"]), 2)
        compared = experiment["report"]["comparison"]
        self.assertEqual({entry["model_id"] for entry in compared}, set(experiment["model_ids"]))
        self.assertEqual(compared[0]["report"]["dataset_sha256"], compared[1]["report"]["dataset_sha256"])
        selected = self.client.post("/api/live/compare", json={"model_ids": experiment["model_ids"]})
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(len(selected.json()["models"]), 2)
        self.assertFalse(self.client.get("/api/status").json()["receiver"]["running"])
        waiting = self.client.get("/api/spectrum").json()["classification_comparison"]
        self.assertEqual(len(waiting), 2)
        self.assertTrue(all(entry["error"] for entry in waiting))
        self.assertEqual(self.client.post("/api/live/compare", json={"model_ids": ["missing-model"]}).status_code, 404)
        start = self.client.post("/api/receiver/start", json={"source": "synthetic", "synthetic_modulation": "AM", "sample_rate": 256000,
            "center_frequency_hz": 125000000, "block_size": 4096, "mode": "monitor", "duration_seconds": .8})
        self.assertEqual(start.status_code, 200, start.text)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            observed = self.client.get("/api/spectrum").json()["classification_comparison"]
            if all((entry.get("classification") or {}).get("confidence_kind") == "uncalibrated_model_score" for entry in observed):
                break
            time.sleep(.02)
        self.assertTrue(all(entry["error"] is None for entry in observed), observed)
        self.assertEqual(observed[0]["sample_rate"], observed[1]["sample_rate"])
        self.assertEqual(observed[0]["classification"]["probabilities"], observed[1]["classification"]["probabilities"])
        self.assertTrue(all(entry["elapsed_ms"] > 0 for entry in observed))
        self.assertFalse(self.client.post("/api/live/compare", json={"model_ids": []}).json()["active"])


if __name__ == "__main__":
    unittest.main()
