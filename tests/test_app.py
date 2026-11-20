"""End-to-end API checks for the portable acquisition / training workflow."""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
import zipfile
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


if __name__ == "__main__":
    unittest.main()
