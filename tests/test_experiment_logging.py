"""Experiment event history, frozen inputs, artifact identity and report API."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from signalclassifier import experiment_log, ml
from signalclassifier.app import create_app
from signalclassifier.sigmf import write_recording


class ExperimentLoggingTests(unittest.TestCase):
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

    def start(self, extra=None):
        config = {"name": "AM/FM <Versuch>", "topology_id": "feature-lda", "samples_per_class": 12,
                  "segment_length": 1024, "snr_db": [20], "jitter": [0], "epochs": 1,
                  "experiment_notes": "Ziel: AM und FM vergleichen.\nMessaufbau: numerische Softwarefixtures.", **(extra or {})}
        response = self.client.post("/api/experiments", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def wait(self, identifier):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = self.client.get(f"/api/experiments/{identifier}").json()
            if value["status"] not in ("queued", "running", "cancelling"):
                return value
            time.sleep(.02)
        self.fail("Experiment did not finish")

    def bundle(self, identifier):
        response = self.client.get(f"/api/experiments/{identifier}/report-bundle")
        self.assertEqual(response.status_code, 200, response.text[:200] if response.status_code != 200 else "")
        self.assertIn("report.zip", response.headers["content-disposition"])
        return zipfile.ZipFile(io.BytesIO(response.content))

    def test_completed_run_records_provenance_events_and_model_checksums(self):
        identifier = self.start()
        state = self.wait(identifier)
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertEqual(state["report"]["execution"]["process_start_method"], "spawn")
        self.assertIn("resource_telemetry", state["report"])
        live = self.client.get("/api/status").json()["telemetry"]
        self.assertTrue(live["cached"])
        self.assertEqual(live["gpu"]["scope"], "whole_device")
        provenance = state["provenance"]
        self.assertEqual(len(provenance["software"]["source_files_sha256"]["ml.py"]), 64)
        self.assertIn("numpy", provenance["software"]["dependencies"])
        self.assertEqual(provenance["configuration_sha256"], experiment_log.canonical_sha256(state["config"]))
        folder = self.root / "experiments" / identifier
        events = experiment_log.read_events(folder / "events.jsonl")
        self.assertEqual(events[0]["event"], "queued")
        self.assertEqual(events[-1]["event"], "completed")
        self.assertEqual([row["sequence"] for row in events], list(range(1, len(events) + 1)))
        model = self.runtime.store.get("model", state["model_id"])
        for name, digest in model["artifact_sha256"].items():
            self.assertEqual(experiment_log.file_sha256(Path(model["model_path"]) / name), digest)
        with self.bundle(identifier) as archive:
            exported = json.loads(archive.read("report.json"))
            self.assertEqual(exported["config"]["experiment_notes"], state["config"]["experiment_notes"])
            self.assertEqual(exported["provenance"], provenance)
            self.assertTrue(exported["export_snapshot"]["complete"])
            self.assertIn(b"queued", archive.read("events.json"))
            self.assertIn(b"source_loading_seconds", archive.read("phase_timings.csv"))
            self.assertIn("resource_usage.csv", archive.namelist())

    def test_api_automatic_search_saves_models_trials_and_report_bundle(self):
        topologies = ["feature-lda"] + (["feature-mlp-16"] if importlib.util.find_spec("torch") else [])
        identifier = self.start({"topology_ids": topologies, "optimization": {
            "enabled": True, "objective": "balanced_accuracy", "max_trials": 2, "timeout_seconds": 0,
            "search_space": {"segment_lengths": [256, 512], "learning_rate_min": .001,
                             "learning_rate_max": .001, "batch_sizes": [4], "epochs": [1]}}})
        state = self.wait(identifier)
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertEqual(state["report"]["selection_objective"], "balanced_accuracy")
        self.assertEqual(len(state["model_ids"]), len(topologies))
        for entry in state["report"]["comparison"]:
            study = entry["report"]["optimization"]
            self.assertEqual(study["completed_trials"], 2)
            self.assertEqual(study["test_evaluations"], 1)
            self.assertIn(study["best_config"]["segment_length"], [256, 512])
            self.assertTrue(all(trial["report"]["metrics"]["accuracy"] is None for trial in study["trials"]))
            self.assertTrue(self.runtime.store.get("model", entry["model_id"])["artifact_sha256"])
        with self.bundle(identifier) as archive:
            exported = json.loads(archive.read("report.json"))
            self.assertEqual(exported["report"]["optimization_summary"]["test_evaluations"], len(topologies))
            self.assertIn(b"balanced_accuracy", archive.read("optimization_trials.csv"))
            self.assertTrue(any(name.startswith("plots/") for name in archive.namelist()))
            events = json.loads(archive.read("events.json"))
            self.assertTrue(any(row["data"].get("optimization_trial") for row in events))
            if len(topologies) > 1:
                self.assertTrue(any("training_loss" in row["data"] for row in events))

    def test_public_configuration_rejects_internal_flags_and_invalid_notes(self):
        for config in ({"_evaluate_test": False}, {"_reference_length": 10**9},
                       {"experiment_notes": []}, {"experiment_notes": "x" * 12001},
                       {"execution": {"mode": "unbounded"}}, {"execution": {"max_workers": 9}},
                       {"execution": {"cpu_threads": -1}}, {"execution": {"preprocessing_workers": True}}):
            response = self.client.post("/api/experiments", json=config)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get("/api/experiments").json(), [])
        self.assertEqual(list((self.root / "experiments").iterdir()), [])

    def test_inventory_remains_original_after_relabeling_and_source_deletion(self):
        for label in ("AM", "FM"):
            write_recording(self.root / "recordings" / label, ml.generate_signal(label, 2048), 8000, 125e6)
            result = self.client.post(f"/api/recordings/{label}/label", json={"label": label, "verified": True})
            self.assertEqual(result.status_code, 200)
        identifier = self.start({"source": "recordings", "recording_ids": ["AM", "FM"], "evaluation_mode": "training_only"})
        state = self.wait(identifier)
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.client.post("/api/recordings/AM/label", json={"label": "Later change", "verified": True})
        preview = self.client.get("/api/recordings/cleanup-preview").json()
        deleted = self.client.post("/api/recordings/delete-batch", json={"recording_ids": ["AM", "FM"], "expected_files": preview["expected_files"]})
        self.assertEqual(deleted.json()["count"], 2)
        with self.bundle(identifier) as archive:
            inventory = json.loads(archive.read("dataset_inventory.json"))
            self.assertEqual({row["label"] for row in inventory}, {"AM", "FM"})
            am = next(row for row in inventory if row["id"] == "AM")
            self.assertEqual(am["annotation"]["label"], "AM")
            self.assertEqual(am["sigmf_metadata"]["global"]["signalclassifier:label"], "AM")
            self.assertIsNone(am["iq_full_content_sha256"])
            self.assertEqual(state["provenance"]["inventory_sha256"], experiment_log.canonical_sha256(inventory))

    def test_failed_search_preserves_structured_trial_events_partial_report_and_inputs(self):
        def fail(config, output, progress):
            partial = {"enabled": True, "trials": [{"trial": 1, "status": "complete", "objective_value": .8}], "stop_reason": "failure"}
            experiment_log.write_json(output / "topology-01-feature-lda" / "optimization.json", partial)
            progress({"progress": .2, "stage": "optimize", "optimization_trial": 1,
                      "epoch": 1, "training_loss": .3, "validation_loss": .4, "config": {"source": "forged"}})
            raise ValueError("Intentional search failure")
        with patch("signalclassifier.app.ml.run_comparison", side_effect=fail):
            identifier = self.start()
            state = self.wait(identifier)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["config"]["source"], "synthetic", "callbacks must not overwrite frozen configuration")
        self.assertEqual(len(state["partial_reports"]), 1)
        with self.bundle(identifier) as archive:
            exported = json.loads(archive.read("report.json"))
            events = json.loads(archive.read("events.json"))
            self.assertEqual(exported["status"], "failed")
            self.assertEqual(exported["partial_reports"][0]["report"]["trials"][0]["objective_value"], .8)
            self.assertTrue(any(row["data"].get("epoch") == 1 and row["data"].get("validation_loss") == .4 for row in events))
            self.assertEqual(events[-1]["event"], "failed")
            self.assertIn("Intentional search failure", archive.read("report.md").decode())

    def test_cancellation_is_journaled_and_partial_logs_are_exportable(self):
        ready, release = threading.Event(), threading.Event()
        def block(config, output, progress):
            progress({"progress": .1, "stage": "optimize", "optimization_trial": 1, "epoch": 1, "training_loss": .5})
            ready.set()
            release.wait(5)
            progress({"progress": .2, "stage": "optimize"})
            raise AssertionError("cancel flag must stop the progress callback")
        try:
            with patch("signalclassifier.app.ml.run_comparison", side_effect=block):
                identifier = self.start()
                self.assertTrue(ready.wait(5))
                response = self.client.post(f"/api/experiments/{identifier}/cancel")
                self.assertEqual(response.status_code, 200)
                release.set()
                state = self.wait(identifier)
            self.assertEqual(state["status"], "cancelled")
            with self.bundle(identifier) as archive:
                events = json.loads(archive.read("events.json"))
                self.assertEqual([row["event"] for row in events][-2:], ["cancel_requested", "cancelled"])
                exported = json.loads(archive.read("report.json"))
                self.assertFalse(exported["export_snapshot"]["complete"])
        finally:
            release.set()

    def test_legacy_export_does_not_invent_historical_hardware_or_labels(self):
        self.runtime.store.put("experiment", "old-run", {"name": "Legacy", "status": "failed", "config": {"source": "recordings"}, "error": "old failure"})
        with self.bundle("old-run") as archive:
            exported = json.loads(archive.read("report.json"))
            self.assertIn("no queue-time provenance", exported["export_snapshot"]["legacy_warning"])
            self.assertEqual(json.loads(archive.read("dataset_inventory.json")), [])
            self.assertNotIn("provenance", exported)

    def test_journal_tail_and_snapshot_budget_are_explicit(self):
        path = self.root / "events.jsonl"
        experiment_log.append_event(path, 1, "progress", {"epoch": 1})
        with path.open("a") as stream:
            stream.write('{"incomplete":')
        events = experiment_log.read_events(path)
        self.assertEqual(events[0]["sequence"], 1)
        self.assertEqual(events[1]["event"], "journal_read_warning")
        experiment_log.append_event(path, 2, "interrupted", {"error": "restarted after crash"})
        events = experiment_log.read_events(path)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[1]["event"], "journal_read_warning")
        self.assertEqual(events[2]["event"], "interrupted")
        meta = write_recording(self.root / "recordings" / "budget", ml.generate_signal("AM", 1024), 256000, 125e6)
        with patch.object(experiment_log, "MAX_INVENTORY_METADATA_BYTES", 1):
            row = experiment_log.dataset_inventory([{"path": str(meta), "id": "budget", "label": "AM", "verified": True}], {})[0]
        self.assertIsNone(row["sigmf_metadata"])
        self.assertTrue(row["metadata_omitted_reason"])
        self.assertEqual(row["metadata_sha256"], hashlib.sha256(Path(meta).read_bytes()).hexdigest())
        self.assertEqual(row["label"], "AM")


if __name__ == "__main__":
    unittest.main()
