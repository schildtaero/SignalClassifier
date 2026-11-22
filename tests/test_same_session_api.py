"""Explicit recording holdout consent, frozen evidence and public disclosure."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from fastapi.testclient import TestClient

from signalclassifier.app import create_app
from signalclassifier import ml, publication
from signalclassifier.sigmf import write_recording


class SameSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.runtime = self.app.state.runtime
        self.runtime.analysis.close()
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def recording(self, label, index, **extras):
        identifier = label.replace(".", "") + "-" + str(index)
        seed = sum(map(ord, label)) * 100 + index
        write_recording(self.root / "recordings" / identifier,
                        ml.generate_signal("FM" if label == "LoRa" else "AM", 4096, seed=seed),
                        256000, 100e6, extra={"sc:session_id": "shared-session", **extras})
        response = self.client.post(f"/api/recordings/{identifier}/label", json={"label": label, "verified": True})
        self.assertEqual(response.status_code, 200, response.text)
        return identifier

    def config(self, ids, **extra):
        return {"source": "recordings", "recording_ids": ids, "topology_id": "feature-lda",
                "segment_length": 1024, "samples_per_class": 12, "epochs": 1,
                "snr_db": [20], "jitter": [0], "device": "cpu", **extra}

    def test_consent_enables_three_classes_and_is_frozen_through_training_and_paper_export(self):
        ids = [self.recording(label, index) for label in ("AX.25", "LoRa", "POCSAG") for index in range(3)]
        config = self.config(ids)
        strict = self.client.post("/api/experiments/validate", json=config).json()
        self.assertFalse(strict["ready"])
        self.assertTrue(all(row["groups"] == 1 for row in strict["classes"]))
        config["allow_same_session"] = True
        relaxed = self.client.post("/api/experiments/validate", json=config).json()
        self.assertTrue(relaxed["ready"], relaxed)
        self.assertEqual(relaxed["split_policy"], "recording_holdout_same_session_allowed")
        self.assertTrue(all(row["groups"] == 3 and row["session_groups"] == 1 for row in relaxed["classes"]))
        admitted = self.client.post("/api/experiments", json=config)
        self.assertEqual(admitted.status_code, 200, admitted.text)
        identifier = admitted.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.client.get(f"/api/experiments/{identifier}").json()
            if state["status"] not in ("queued", "running", "cancelling"):
                break
            time.sleep(.025)
        self.assertEqual(state["status"], "completed", state.get("error"))
        report = state["report"]
        self.assertTrue(state["config"]["allow_same_session"])
        self.assertEqual(report["split_policy"], relaxed["split_policy"])
        self.assertFalse(report["validation_independence"]["session_independent"])
        self.assertEqual(len(report["recording_group_memberships"]), 9)
        splits = list(report["split_groups"].values())
        self.assertTrue(all(len(groups) == 3 for groups in splits))
        self.assertFalse(set(splits[0]) & set(splits[1]) | set(splits[0]) & set(splits[2]) | set(splits[1]) & set(splits[2]))
        folder = self.root / "experiments" / identifier
        frozen = json.loads((folder / "configuration.json").read_text())
        self.assertTrue(frozen["allow_same_session"])
        inventory = json.loads((folder / "dataset-inventory.json").read_text())
        self.assertEqual(len({row["group_id"] for row in inventory}), 1)
        self.assertEqual(len({row["recording_group_id"] for row in inventory}), 9)
        model = self.runtime.store.all("model")[0]
        self.assertFalse(model["validation_independence"]["session_independent"])
        bundle = self.client.get(f"/api/experiments/{identifier}/report-bundle")
        self.assertEqual(bundle.status_code, 200, bundle.text if bundle.status_code != 200 else "")
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            markdown = "\n".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".md"))
        self.assertIn("Gleiche Sitzung ausdrücklich zugelassen", markdown)
        package = self.runtime.paper_package(identifier)
        public = json.loads(package["experiment.json"])
        self.assertTrue(public["config"]["allow_same_session"])
        self.assertFalse(public["validation_independence"]["session_independent"])
        self.assertEqual(len(public["recording_group_memberships"]), 9)

    def test_parent_chain_is_resolved_even_when_only_derived_recordings_are_selected(self):
        ids = []
        for label in ("AM", "FM"):
            parent = self.recording(label, 0)
            for index in range(1, 4):
                child = self.recording(label, index, **{"sc:parent_recording_id": parent})
                ids.append(child)
                parent = child
        result = self.client.post("/api/experiments/validate", json=self.config(ids, allow_same_session=True)).json()
        self.assertFalse(result["ready"])
        self.assertTrue(all(row["groups"] == 1 for row in result["classes"]), result)

    def test_consent_requires_boolean_and_defaults_remain_strict(self):
        for invalid in ("true", 1, None, [], {}):
            result = self.client.post("/api/experiments/validate", json=self.config([], allow_same_session=invalid)).json()
            self.assertFalse(result["ready"])
            self.assertIn("boolescher", " ".join(result["errors"]))

    def test_public_projection_discloses_consent_without_copying_user_fields(self):
        from test_publication import fixture
        experiment, inventory, events = fixture()
        experiment = copy.deepcopy(experiment)
        experiment["config"]["allow_same_session"] = True
        experiment["report"]["config"]["allow_same_session"] = True
        experiment["report"]["validation_independence"] = {"note": "/private/DO_NOT_PUBLISH", "session_independent": True}
        data = json.loads(publication.make_publication_package(experiment, dataset_inventory=inventory, events=events)["experiment.json"])
        self.assertFalse(data["validation_independence"]["session_independent"])
        self.assertFalse(data["models"][0]["validation_independence"]["session_independent"])
        self.assertNotIn("DO_NOT_PUBLISH", json.dumps(data))
