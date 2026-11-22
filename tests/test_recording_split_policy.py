import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from signalclassifier import ml
from signalclassifier.sigmf import SigMFWriter
from tests.test_ml import inline_execution


class RecordingSplitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        ml._recording_content_digest.cache_clear()
        self.entries = []
        for label in ("AM", "FM"):
            for index in range(3):
                identifier = f"{label}-{index}"
                rng = np.random.default_rng(index + (0 if label == "AM" else 100))
                iq = (rng.normal(size=1024) + 1j * rng.normal(size=1024)).astype(np.complex64)
                writer = SigMFWriter(self.root / identifier, 256000, 100e6,
                                     extra={"sc:session_id": "shared-session"})
                writer.append(iq); path = writer.close()
                self.entries.append({"id": identifier, "metadata_path": str(path), "group_id": "shared-session", "label": label, "verified": True})

    def config(self, **kwargs):
        return {"source": "recordings", "recordings": self.entries, "segment_length": 256,
                "samples_per_class": 12, "snr_db": [20], "jitter": [0], "device": "cpu",
                "epochs": 1, "batch_size": 4, **kwargs}

    def metadata(self, entry, **values):
        path = Path(entry["metadata_path"])
        data = json.loads(path.read_text())
        data["global"].update(values)
        path.write_text(json.dumps(data))

    def test_strict_default_still_requires_separate_sessions_and_does_not_hash_iq(self):
        with mock.patch.object(ml, "_recording_content_digest", side_effect=AssertionError("strict preflight must not hash IQ")) as digest:
            ready = ml.dataset_readiness(self.config())
        digest.assert_not_called()
        self.assertFalse(ready["ready"])
        self.assertEqual(ready["split_policy"], "session_holdout")
        self.assertEqual({row["groups"] for row in ready["classes"]}, {1})
        with self.assertRaises(ValueError):
            ml._validate_config(self.config(allow_same_session="yes"))
        self.assertFalse(ml.dataset_readiness(self.config(allow_same_session="yes"))["ready"])

    def test_relaxed_policy_splits_recordings_not_segments_and_preserves_session_counts(self):
        config = ml._validate_config(self.config(allow_same_session=True))
        ready = ml.dataset_readiness(config)
        self.assertTrue(ready["ready"], ready["errors"])
        self.assertEqual(ready["split_policy"], "recording_holdout_same_session_allowed")
        self.assertEqual([(row["groups"], row["session_groups"]) for row in ready["classes"]], [(3, 1), (3, 1)])
        self.assertEqual(len(ready["recording_group_memberships"]), 6)
        self.assertEqual({row["session_group_id"] for row in ready["recording_group_memberships"]}, {"shared-session"})
        groups = ml._recording_groups(config, 256)
        splits = [{entry["group_id"] for entry in rows} for rows in groups.values()]
        self.assertFalse(splits[0] & splits[1] or splits[0] & splits[2] or splits[1] & splits[2])
        self.assertEqual({entry["session_group_id"] for rows in groups.values() for entry in rows}, {"shared-session"})
        self.assertEqual(ml._planned_split_groups(config), {split: sorted({entry["group_id"] for entry in rows}) for split, rows in groups.items()})

    def test_exact_copy_with_forged_new_origin_still_belongs_to_original_split(self):
        source = self.entries[0]
        target = self.root / "renamed-copy.sigmf-meta"
        doc = json.loads(Path(source["metadata_path"]).read_text())
        doc["global"].update({"sc:recording_id": "new-copy-id", "sc:origin_recording_id": "new-copy-origin"})
        target.write_text(json.dumps(doc))
        shutil.copyfile(Path(source["metadata_path"]).with_suffix(".sigmf-data"), target.with_suffix(".sigmf-data"))
        self.entries.append({**source, "id": "new-copy-id", "metadata_path": str(target), "recording_group_id": "new-copy-origin"})
        config = ml._validate_config(self.config(allow_same_session=True))
        inventory = ml._config_recording_inventory(config, self.entries)
        self.assertEqual(inventory[0]["group_id"], inventory[-1]["group_id"])
        ready = ml.dataset_readiness(config)
        self.assertEqual(next(row for row in ready["classes"] if row["label"] == "AM")["groups"], 3)
        groups = ml._recording_groups(config, 256)
        owners = [split for split, rows in groups.items() if any(row["recording_group_id"] in {"AM-0", "new-copy-origin"} for row in rows)]
        self.assertEqual(len(owners), 1)

    def test_parent_chain_and_cross_label_origin_are_never_separated(self):
        self.metadata(self.entries[1], **{"sc:parent_recording_id": "AM-0"})
        self.metadata(self.entries[2], **{"sc:parent_recording_id": "AM-1"})
        self.metadata(self.entries[3], **{"sc:origin_recording_id": "AM-0"})
        config = ml._validate_config(self.config(allow_same_session=True))
        inventory = ml._config_recording_inventory(config, self.entries)
        self.assertEqual(len({entry["group_id"] for entry in inventory[:4]}), 1)
        ready = ml.dataset_readiness(config)
        self.assertFalse(ready["ready"])
        self.assertEqual(next(row for row in ready["classes"] if row["label"] == "AM")["groups"], 1)

    def test_canonical_file_aliases_and_hash_cache_are_bounded(self):
        config = ml._validate_config(self.config(allow_same_session=True))
        alias = {**self.entries[0], "id": "alias", "recording_group_id": "different-origin",
                 "metadata_path": str(self.root / "." / "AM-0.sigmf-meta")}
        inventory = ml._config_recording_inventory(config, [self.entries[0], alias])
        self.assertEqual(inventory[0]["group_id"], inventory[1]["group_id"])
        before = ml._recording_content_digest.cache_info()
        ml._recording_inventory(self.entries[0], allow_same_session=True)
        after = ml._recording_content_digest.cache_info()
        self.assertEqual(after.maxsize, 1024)
        self.assertGreater(after.hits, before.hits)

    def test_changed_file_hash_is_rejected_without_identity_fallback(self):
        path = str(Path(self.entries[0]["metadata_path"]).with_suffix(".sigmf-data"))
        signature = ml._recording_signature(path)
        changed = (*signature[:-1], signature[-1] + 1)
        with mock.patch.object(ml, "_recording_signature", side_effect=[signature, changed]):
            with self.assertRaisesRegex(ValueError, "verändert"):
                ml._recording_content_digest(path, signature)

    def test_optimized_baseline_saves_recording_holdout_policy_and_disjoint_groups(self):
        config = self.config(allow_same_session=True, optimization={"enabled": True, "max_trials": 2, "timeout_seconds": 0,
                             "search_space": {"segment_lengths": [256, 512], "batch_sizes": [4], "epochs": [1]}})
        with mock.patch.object(ml, "execute_jobs", side_effect=inline_execution):
            report = ml.run_experiment(config, self.root / "experiment")
        self.assertEqual(report["split_policy"], "recording_holdout_same_session_allowed")
        self.assertFalse(report["validation_independence"]["session_independent"])
        self.assertTrue(report["validation_independence"]["recording_disjoint"])
        self.assertEqual(report["evaluation_scope"], "validation_and_test")
        self.assertEqual(report["optimization"]["split_policy"], report["split_policy"])
        self.assertEqual(report["recording_group_memberships"], report["readiness"]["recording_group_memberships"])
        self.assertEqual(report["recording_group_memberships"], report["optimization"]["recording_group_memberships"])
        for trial in report["optimization"]["trials"]:
            self.assertEqual(trial["report"]["split_groups"], report["split_groups"])
            self.assertEqual(trial["report"]["evaluation_scope"], "validation_only")
        manifest = ml.load_model_metadata(report["model_path"])
        self.assertTrue(manifest["allow_same_session"])
        self.assertEqual(manifest["split_policy"], report["split_policy"])
        self.assertTrue(any("kein Nachweis unabhängiger" in text for text in report["limitations"]))

    def test_training_only_and_synthetic_do_not_claim_independent_recording_sessions(self):
        policy = ml._split_metadata({"source": "recordings", "evaluation_mode": "training_only", "allow_same_session": True})
        self.assertIsNone(policy["validation_independence"]["session_independent"])
        self.assertIsNone(policy["validation_independence"]["recording_disjoint"])
        policy = ml._split_metadata({"source": "synthetic"})
        self.assertEqual(policy["split_policy"], "synthetic_origin_holdout")
        self.assertIsNone(policy["validation_independence"]["session_independent"])

    def test_prefixed_replay_origin_and_legacy_original_share_group_without_app_resolution(self):
        writer = SigMFWriter(self.root / "later-replay", 256000, 100e6,
                             extra={"sc:session_id": "different-session", "sc:origin_recording_id": "recording:AM-0"})
        writer.append(np.arange(1024, dtype=np.float32).astype(np.complex64))
        path = writer.close()
        replay = {"id": "later-replay", "metadata_path": str(path), "group_id": "different-session", "label": "AM", "verified": True}
        config = ml._validate_config(self.config(allow_same_session=True))
        before = ml._config_recording_inventory(config, [self.entries[0]])[0]
        inventory = ml._config_recording_inventory(config, [self.entries[0], replay])
        self.assertNotEqual(inventory[0]["recording_sha256"], inventory[1]["recording_sha256"])
        self.assertEqual(inventory[0]["group_id"], inventory[1]["group_id"])
        self.assertEqual(inventory[0]["group_id"], before["group_id"])
        self.assertEqual({entry["session_group_id"] for entry in inventory}, {"shared-session", "different-session"})
        strict = ml._config_recording_inventory({**config, "allow_same_session": False}, [self.entries[0], replay])
        self.assertNotEqual(strict[0]["group_id"], strict[1]["group_id"])

    def test_explicitly_unresolved_lineage_refuses_relaxed_split_but_not_session_holdout(self):
        self.metadata(self.entries[0], **{"sc:origin_recording_resolution": {
            "resolved": False, "method": "legacy_parent_chain", "reason": "missing_ancestor"}})
        with mock.patch.object(ml, "_recording_content_digest", side_effect=AssertionError("unresolved origin must not hash")):
            with self.assertRaisesRegex(ValueError, "ausdrücklich ungeklärt"):
                ml._recording_inventory(self.entries[0], allow_same_session=True)
            ml._recording_inventory(self.entries[0])
        entries = [{**entry, "group_id": f"session-{index}"} for index, entry in enumerate(self.entries)]
        strict = ml.dataset_readiness(self.config(recordings=entries))
        self.assertTrue(strict["ready"], strict["errors"])
        relaxed = ml.dataset_readiness(self.config(recordings=entries, allow_same_session=True))
        self.assertFalse(relaxed["ready"])
        self.assertTrue(any("ausdrücklich ungeklärt" in error for error in relaxed["errors"]))
        # Only a dict carrying the actual boolean False is the explicit marker.
        self.metadata(self.entries[0], **{"sc:origin_recording_resolution": {"resolved": 0}})
        ml._recording_inventory(self.entries[0], allow_same_session=True)


if __name__ == "__main__":
    unittest.main()
