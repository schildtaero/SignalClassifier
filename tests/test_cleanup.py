"""Reviewed short-recording deletion, threshold boundaries and failure recovery."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from signalclassifier.app import create_app
from signalclassifier.sigmf import write_recording


class RecordingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "library"
        self.app = create_app(self.root)
        self.runtime = self.app.state.runtime
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def recording(self, name, samples=800, datatype="cf32_le"):
        path = Path(write_recording(self.root / "recordings" / name, np.ones(samples, dtype=np.complex64), 8000, 125e6))
        if datatype == "ci16_le":
            doc = json.loads(path.read_text())
            doc["global"]["core:datatype"] = datatype
            path.write_text(json.dumps(doc))
            np.zeros(samples * 2, dtype="<i2").tofile(path.with_suffix(".sigmf-data"))
        return path

    def preview(self):
        response = self.client.get("/api/recordings/cleanup-preview", params={"max_duration_seconds": .5})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def delete(self, preview, ids=None):
        return self.client.post("/api/recordings/delete-batch", json={
            "recording_ids": ids if ids is not None else [row["id"] for row in preview["recordings"]],
            "max_duration_seconds": .5, "expected_files": preview["expected_files"]})

    def test_strict_half_second_boundary_deletes_pairs_and_labels_only_after_review(self):
        short = self.recording("short", 3999)
        ci16 = self.recording("short-int16", 1000, "ci16_le")
        boundary = self.recording("half-second", 4000)
        long = self.recording("long", 4001)
        unpublished = self.root / "recordings" / "open.sigmf-data"
        unpublished.write_bytes(b"open writer placeholder")
        label = self.client.post("/api/recordings/short/label", json={"label": "AM", "verified": True})
        self.assertEqual(label.status_code, 200)
        self.runtime.store.put("experiment", "old", {"status": "completed", "config": {"recording_ids": ["short"]}})
        preview = self.preview()
        self.assertIsNone(preview["blocked_reason"])
        self.assertEqual({row["id"] for row in preview["recordings"]}, {"short", "short-int16"})
        am = next(row for row in preview["recordings"] if row["id"] == "short")
        self.assertEqual(am["label"], "AM")
        self.assertTrue(am["verified"])
        self.assertEqual(am["experiment_count"], 1)
        self.assertEqual(am["duration_seconds"], 3999 / 8000)
        self.assertTrue(short.exists(), "preview must not mutate files")
        self.assertEqual(len(self.client.get("/api/labels").json()), 1)
        response = self.delete(preview)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(response.json()["bytes_deleted"], preview["total_bytes"])
        for path in (short, ci16):
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".sigmf-data").exists())
        self.assertTrue(boundary.exists())
        self.assertTrue(long.exists())
        self.assertTrue(unpublished.exists())
        self.assertEqual(self.client.get("/api/labels").json(), [])
        self.assertEqual(self.runtime.store.get("experiment", "old")["status"], "completed")
        self.assertEqual(list((self.root / "recordings").glob(".delete-*")), [])

    def test_explicit_preview_ids_do_not_include_new_files_and_changed_files_are_skipped(self):
        first = self.recording("first")
        unchanged = self.recording("unchanged")
        changed_iq = self.recording("changed-iq")
        preview = self.preview()
        late = self.recording("late-arrival")
        doc = json.loads(first.read_text())
        doc["global"]["core:description"] = "Changed since review"
        first.write_text(json.dumps(doc))
        with changed_iq.with_suffix(".sigmf-data").open("r+b") as stream:
            stream.write(b"\x00" * 8)
        response = self.delete(preview)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted"], ["unchanged"])
        self.assertEqual({row["id"] for row in response.json()["skipped"]}, {"first", "changed-iq"})
        self.assertTrue(first.exists())
        self.assertTrue(changed_iq.exists())
        self.assertTrue(late.exists())
        self.assertFalse(unchanged.exists())

    def test_unknown_id_invalid_id_or_missing_review_cannot_partially_delete(self):
        path = self.recording("short")
        preview = self.preview()
        for identifier in ("missing", "../outside"):
            response = self.delete(preview, ["short", identifier])
            self.assertIn(response.status_code, (400, 404), response.text)
            self.assertTrue(path.exists())
        response = self.client.post("/api/recordings/delete-batch", json={"recording_ids": ["short"]})
        self.assertEqual(response.status_code, 422)
        response = self.delete({**preview, "expected_files": {"short": "stale"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertTrue(path.exists())
        for threshold in ("nan", "inf", "0", "0.50001"):
            self.assertEqual(self.client.get("/api/recordings/cleanup-preview", params={"max_duration_seconds": threshold}).status_code, 400)

    def test_running_experiment_and_newly_longer_files_are_rechecked(self):
        short = self.recording("protected")
        growing = self.recording("growing")
        preview = self.preview()
        self.runtime.store.put("experiment", "active", {"status": "queued", "config": {"recordings": [{"id": "protected"}]}})
        np.ones(4000, dtype=np.complex64).tofile(growing.with_suffix(".sigmf-data"))
        result = self.delete(preview)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["count"], 0)
        self.assertEqual(len(result.json()["skipped"]), 2)
        self.assertTrue(short.exists())
        self.assertTrue(growing.exists())
        checked = self.preview()
        self.assertEqual(checked["recordings"], [])
        self.assertIn("Experiment", checked["skipped"][0]["reason"])

    def test_receiver_and_lingering_error_thread_block_deletion(self):
        path = self.recording("short")
        preview = self.preview()
        response = self.client.post("/api/receiver/start", json={"source": "synthetic", "mode": "monitor", "duration_seconds": 60})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(self.preview()["blocked_reason"])
        self.assertEqual(self.delete(preview).status_code, 409)
        self.assertTrue(path.exists())
        self.client.post("/api/receiver/stop")
        with patch.object(self.runtime.capture, "_state", "error"), patch.object(self.runtime.capture._thread, "is_alive", return_value=True):
            self.assertTrue(self.runtime.capture.status()["busy"])
            self.assertEqual(self.delete(preview).status_code, 409)
        self.assertIsNone(self.preview()["blocked_reason"])

    def test_file_or_database_failure_restores_pairs_and_labels(self):
        first = self.recording("first")
        second = self.recording("second")
        self.client.post("/api/recordings/first/label", json={"label": "AM", "verified": True})
        originals = {path: path.read_bytes() for path in (first, second, first.with_suffix(".sigmf-data"), second.with_suffix(".sigmf-data"))}
        original_replace = Path.replace

        def fail_data_move(source, target):
            if source == second.with_suffix(".sigmf-data"):
                raise OSError("simulated file move failure")
            return original_replace(source, target)

        for failure in (patch.object(Path, "replace", fail_data_move),
                        patch.object(self.runtime.store, "delete_many", side_effect=OSError("simulated database failure")),
                        patch.object(self.runtime.store, "delete_many", side_effect=sqlite3.OperationalError("database is locked"))):
            preview = self.preview()
            with failure:
                response = self.delete(preview)
            self.assertEqual(response.status_code, 503, response.text)
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content)
            self.assertEqual(len(self.client.get("/api/labels").json()), 1)
            self.assertEqual(list((self.root / "recordings").glob(".delete-*")), [])

    def test_purge_failure_reports_committed_removal_and_remaining_temporary_data(self):
        path = self.recording("short")
        preview = self.preview()
        original_unlink = Path.unlink

        def fail_data_unlink(source, *args, **kwargs):
            if source.parent.name.startswith(".delete-") and source.suffix == ".sigmf-data":
                raise OSError("simulated unlink failure")
            return original_unlink(source, *args, **kwargs)

        with patch.object(Path, "unlink", fail_data_unlink):
            response = self.delete(preview)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 1)
        self.assertIsNotNone(response.json()["cleanup_warning"])
        self.assertLess(response.json()["bytes_deleted"], preview["total_bytes"])
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get("/api/recordings").json(), [])
        self.assertEqual(len(list((self.root / "recordings").glob(".delete-*/*.sigmf-data"))), 1)


if __name__ == "__main__":
    unittest.main()
