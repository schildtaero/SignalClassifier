"""Fake-device HTTP workflow through acquisition, fixed cohorts and reports."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from signalclassifier.app import create_app
from test_generation import FakeTx, FakeRx


class LoopbackRx(FakeRx):
    def read(self, count):
        indices = np.arange(count)+self.position
        self.position += count
        source = self.tx.iq
        positions = np.floor(indices*self.tx.config["sample_rate"]/self.sample_rate).astype(np.int64) % len(source)
        return (source[positions]*.25).astype(np.complex64)


class GeneratorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.runtime = self.app.state.runtime
        self.runtime.analysis.close()
        self.runtime.generator._tx_factory = FakeTx
        self.runtime.generator._rx_factory = LoopbackRx
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        FakeTx.instances.clear()

    def config(self):
        return {"mode_ids": ["am", "nfm"], "variants_per_mode": 1, "payload_repetitions": 3,
                "duration_seconds": .5, "settle_seconds": .05, "rx_preroll_seconds": .05,
                "transmitter": {"uri": "ip:PRIVATE_TX_URI_CANARY", "attenuation_offsets_db": [0]}, "receiver": {"source": "rsp1a", "sample_rate": 520834}}

    def test_catalogue_plan_and_unconfirmed_start_are_hardware_free(self):
        response = self.client.get("/api/generator/catalog")
        self.assertEqual(response.status_code, 200)
        catalogue = response.json()
        self.assertGreaterEqual(len(catalogue["schemes"]), 331)
        self.assertEqual(sum(row.get("provider") == "native" for row in catalogue["schemes"]), 30)
        plan = self.client.post("/api/generator/plan", json=self.config()).json()
        self.assertTrue(plan["ready"], plan["errors"])
        rejected = self.client.post("/api/generator/start", json={"plan_id": plan["id"], "plan_sha256": plan["sha256"], "transmit_enabled": False, "setup_confirmed": True})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(FakeTx.instances, [])

    def test_receiver_and_scan_are_reserved_and_hot_status_avoids_archive_scans(self):
        with patch.object(self.runtime.generator, "status", return_value={"busy": True, "base_counts": {"recordings": 5, "labels": 3},
                "recordings_saved_count": 2, "verified_recordings_count": 1}), \
             patch.object(self.runtime.capture, "list_recordings", side_effect=AssertionError("no metadata archive scan during capture")):
            self.assertEqual(self.client.post("/api/receiver/start", json={"source": "rsp1a"}).status_code, 409)
            self.assertEqual(self.client.post("/api/receiver/stop").status_code, 409)
            self.assertEqual(self.client.post("/api/analysis/scan/start", json={}).status_code, 409)
            response = self.client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["counts"]["recordings"], 7)
            self.assertEqual(response.json()["counts"]["labels"], 4)

    def test_fake_loopback_labels_train_fixed_payload_splits_and_retain_reference_evidence(self):
        plan = self.client.post("/api/generator/plan", json=self.config()).json()
        response = self.client.post("/api/generator/start", json={"plan_id": plan["id"], "plan_sha256": plan["sha256"],
                                                              "transmit_enabled": True, "setup_confirmed": True})
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()["id"]
        self.runtime.generator._thread.join(timeout=10)
        state = self.client.get("/api/generator/status").json()
        self.assertEqual(state["status"], "completed", state.get("error"))
        self.assertEqual(state["verified_recordings_count"], 6)
        self.assertFalse(state["tx_active"])
        records = self.client.get("/api/recordings").json()
        self.assertEqual(len(records), 6)
        self.assertTrue(all(row["annotation"]["verification_kind"] == "generator_reference" for row in records))
        self.assertTrue(all(row["annotation"]["human_verified"] is False for row in records))
        experiment = {"source": "recordings", "recording_ids": [row["id"] for row in records], "allow_same_session": True,
            "respect_generation_splits": True, "topology_id": "feature-lda", "segment_length": 1024, "samples_per_class": 12,
            "epochs": 1, "snr_db": [20], "jitter": [0], "device": "cpu"}
        readiness = self.client.post("/api/experiments/validate", json=experiment).json()
        self.assertTrue(readiness["ready"], readiness)
        self.assertEqual(readiness["split_source"], "generator_plan")
        response = self.client.post("/api/experiments", json=experiment)
        self.assertEqual(response.status_code, 200, response.text)
        experiment_id = response.json()["id"]
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            result = self.client.get(f"/api/experiments/{experiment_id}").json()
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(.03)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["report"]["label_evidence"], "generator_provenance")
        self.assertEqual(result["report"]["generator_reference_summary"]["count"], 6)
        self.assertFalse(result["report"]["generator_reference_summary"]["rx_class_verified"])
        manifest = self.client.get(f"/api/generator/runs/{identifier}/download")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(len(manifest.json()["records"]), 6)
        inventory = json.loads((self.root / "experiments" / experiment_id / "dataset-inventory.json").read_text())
        self.assertTrue(all(row["generation"]["reference_kind"] == "controlled_generator_reference" for row in inventory))
        self.assertTrue(all(row["synthetic"] for row in inventory))
        public_files = self.runtime.paper_package(experiment_id)
        public = json.loads(public_files["experiment.json"])
        dataset = json.loads(public_files["dataset.json"])
        self.assertTrue(public["config"]["respect_generation_splits"])
        self.assertEqual(public["label_evidence"], "generator_provenance")
        self.assertFalse(public["generator_reference_summary"]["rx_class_verified"])
        self.assertTrue(all(row["generation"]["transport"] == "cable" for row in dataset["recordings"]))
        self.assertTrue(all(row["generation"]["tx_gain_baseline_db"] == -50 for row in dataset["recordings"]))
        self.assertNotIn(b"PRIVATE_TX_URI_CANARY", b"".join(public_files.values()))
