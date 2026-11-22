"""Opt-in publication is isolated from training and never needs live GitHub."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from signalclassifier.app import create_app


class PublicationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.runtime = self.app.state.runtime
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def start(self, **extra):
        response = self.client.post("/api/experiments", json={
            "topology_id": "feature-lda", "samples_per_class": 12, "segment_length": 1024,
            "snr_db": [20], "jitter": [0], "epochs": 1, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def wait(self, identifier, publication=False):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = self.client.get(f"/api/experiments/{identifier}").json()
            if value["status"] not in ("queued", "running", "cancelling") and (
                    not publication or value.get("publication", {}).get("status") not in ("queued", "publishing")):
                return value
            time.sleep(.02)
        self.fail("bounded publication/run wait exceeded")

    def test_default_is_off_and_public_package_is_downloadable_without_upload(self):
        marker = "PRIVATE-NOTES-GPS-DO-NOT-PUBLISH"
        with patch("signalclassifier.publication.publish_package") as upload:
            identifier = self.start(experiment_notes=marker)
            state = self.wait(identifier)
            self.assertEqual(state["status"], "completed", state.get("error"))
            self.assertEqual(state["publication"]["status"], "disabled")
            upload.assert_not_called()
            self.assertEqual(self.client.post(f"/api/experiments/{identifier}/publish").status_code, 400)
            response = self.client.get(f"/api/experiments/{identifier}/paper-bundle")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertGreater(len(archive.namelist()), 1)
            for name in archive.namelist():
                self.assertFalse(name.endswith((".sigmf-data", ".sigmf-meta", ".pt", ".npz")))
                self.assertNotIn(marker.encode(), archive.read(name))
                self.assertNotIn(str(self.root).encode(), archive.read(name))

    def test_upload_failure_does_not_fail_training_and_retry_is_isolated(self):
        with patch("signalclassifier.publication.publish_package", side_effect=RuntimeError("private-token-secret")) as upload:
            identifier = self.start(github_publication={"enabled": True})
            state = self.wait(identifier, publication=True)
            self.assertEqual(state["status"], "completed", state.get("error"))
            self.assertEqual(state["publication"]["status"], "failed")
            self.assertNotIn("private-token-secret", json.dumps(state["publication"]))
            upload.assert_called_once()
        release, entered = threading.Event(), threading.Event()
        def publish(settings, run_id, files):
            entered.set()
            self.assertEqual(run_id, identifier)
            self.assertTrue(files)
            release.wait(3)
            return {"commit_sha": "a" * 40, "url": "https://github.com/example/results/commit/" + "a" * 40,
                    "files": sorted(files)}
        try:
            with patch("signalclassifier.publication.publish_package", side_effect=publish) as upload:
                response = self.client.post(f"/api/experiments/{identifier}/publish")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(entered.wait(2))
                again = self.client.post(f"/api/experiments/{identifier}/publish")
                self.assertEqual(again.status_code, 200)
                release.set()
                published = self.wait(identifier, publication=True)
                self.assertEqual(published["publication"]["status"], "published")
                self.client.post(f"/api/experiments/{identifier}/publish")
                upload.assert_called_once()
        finally:
            release.set()

    def test_running_run_and_unreviewed_credentials_are_not_publishable(self):
        self.runtime.store.put("experiment", "running", {"status": "running", "config": {"github_publication": {"enabled": True}}})
        self.assertEqual(self.client.get("/api/experiments/running/paper-bundle").status_code, 409)
        self.assertEqual(self.client.post("/api/experiments/running/publish").status_code, 409)
        response = self.client.post("/api/experiments/validate", json={"github_publication": {"enabled": True, "token": "private"}})
        self.assertFalse(response.json()["ready"])
        with patch.dict("os.environ", {"SIGNALCLASSIFIER_GITHUB_TOKEN": "private-env-token"}):
            status = self.client.get("/api/publication/status").json()
            self.assertTrue(status["configured"])
            self.assertNotIn("private-env-token", json.dumps(status))

    def test_failed_experiment_can_publish_partial_results(self):
        with patch("signalclassifier.app.ml.run_comparison", side_effect=ValueError("fixture failure")), \
             patch("signalclassifier.publication.publish_package", return_value={"files": ["results.json"]}) as upload:
            identifier = self.start(github_publication={"enabled": True})
            state = self.wait(identifier, publication=True)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["publication"]["status"], "published")
            upload.assert_called_once()

    def test_restart_marks_upload_interrupted_without_resuming_network(self):
        self.runtime.store.put("experiment", "old", {"status": "completed", "config": {"github_publication": {"enabled": True}},
            "publication": {"status": "publishing"}})
        with patch("signalclassifier.publication.publish_package") as upload:
            other = create_app(self.root)
            try:
                state = other.state.runtime.store.get("experiment", "old")
                self.assertEqual(state["status"], "completed")
                self.assertEqual(state["publication"]["status"], "interrupted")
                upload.assert_not_called()
            finally:
                other.state.runtime.close()


if __name__ == "__main__":
    unittest.main()
