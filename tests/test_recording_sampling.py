import collections
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from signalclassifier import ml
from signalclassifier.sigmf import SigMFWriter


class RecordingSamplingTests(unittest.TestCase):
    def entries(self, count, samples=4_194_304):
        return [{"id": f"file-{index:04d}", "label": "AM" if index % 2 == 0 else "FM",
                 "verified": True, "group_id": f"group-{index}",
                 "path": f"/virtual/file-{index:04d}.sigmf-meta", "sample_count": samples}
                for index in range(count)]

    @staticmethod
    def inventory(entry, **_):
        return {**entry, "metadata_path": entry["path"], "sample_rate": 256000,
                "recording_group_id": entry["id"], "session_group_id": entry["group_id"]}

    def config(self, entries, **values):
        return {"source": "recordings", "recordings": entries, "evaluation_mode": "training_only",
                "use_all_recordings": True, "segment_length": 1024, "samples_per_class": 64,
                "snr_db": [20], "jitter": [0], "device": "cpu", "epochs": 1, "batch_size": 4, **values}

    def test_mode_is_strict_opt_in_and_never_enables_evaluated_sampling_or_hpo(self):
        entries = self.entries(2)
        self.assertIs(ml._validate_config({})["use_all_recordings"], False)
        for values in ({"use_all_recordings": "true"}, {"use_all_recordings": 1},
                       {"source": "synthetic"}, {"evaluation_mode": "independent"}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ml._validate_config(self.config(entries, **values))
                self.assertFalse(ml.dataset_readiness(self.config(entries, **values))["ready"])
        with self.assertRaisesRegex(ValueError, "Validierung"):
            ml._validate_config(self.config(entries, optimization={"enabled": True}))

    def test_metadata_plan_reduces_per_file_quota_for_128_mib_without_raising_budget(self):
        entries = self.entries(5)
        config = ml._validate_config(self.config(entries, segment_length=65536))
        with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory), \
             mock.patch("signalclassifier.sigmf.read_recording", side_effect=AssertionError("metadata only")):
            result = ml.dataset_readiness(config)
            inventory = ml._config_recording_inventory(config, entries)
            quotas, summary = ml._recording_quota_plan(config, inventory, 65536)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["sampling"], summary)
        self.assertEqual(sum(quotas), 256)
        self.assertEqual(sorted(quotas), [51, 51, 51, 51, 52])
        self.assertTrue(summary["capped"])
        self.assertTrue(summary["all_recordings_represented"])
        self.assertEqual(summary["recordings"], 5)
        self.assertEqual(summary["segments"], 256)
        self.assertEqual(summary["available_segments"], 320)
        self.assertEqual(summary["retained_iq_bytes"], 128 * 1024 ** 2)
        self.assertEqual(summary["budget_iq_bytes"], 128 * 1024 ** 2)
        self.assertEqual(summary["segment_limit"], ml.MAX_SEGMENTS_PER_SPLIT)
        self.assertTrue(any("reduziert" in warning for warning in result["warnings"]))

    def test_4096_segment_cap_keeps_all_1000_files_and_plan_is_order_independent(self):
        entries = self.entries(1000)
        config = ml._validate_config(self.config(entries, segment_length=256))
        inventory = [self.inventory(entry) for entry in entries]
        quotas, summary = ml._recording_quota_plan(config, inventory, 256)
        reversed_inventory = inventory[::-1]
        reversed_quotas, reversed_summary = ml._recording_quota_plan(config, reversed_inventory, 256)
        self.assertEqual(dict(zip((x["id"] for x in inventory), quotas)),
                         dict(zip((x["id"] for x in reversed_inventory), reversed_quotas)))
        self.assertEqual(summary, reversed_summary)
        self.assertEqual(summary["segments"], 4096)
        self.assertEqual(summary["recordings"], 1000)
        self.assertEqual(set(quotas), {4, 5})
        self.assertLess(summary["retained_iq_bytes"], summary["budget_iq_bytes"])

    def test_loader_and_readiness_use_exactly_the_same_complete_windows_for_every_file(self):
        entries = self.entries(100, samples=65536)
        entries[0]["sample_count"] = 1537  # one usable window; no padding/tail read
        config = ml._validate_config(self.config(entries))
        reads = {}
        def read(path, start=0, count=None):
            reads[path] = (start, count)
            return np.ones(count, dtype=np.complex64), {"global": {"core:sample_rate": 256000}}
        with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory):
            readiness = ml.dataset_readiness(config)
            inventory = ml._config_recording_inventory(config, entries)
            quotas, _ = ml._recording_quota_plan(config, inventory, 1024)
            with mock.patch("signalclassifier.sigmf.read_recording", side_effect=read):
                groups = ml._recording_groups(config, 1024)
        self.assertTrue(readiness["ready"], readiness["errors"])
        self.assertEqual(len(reads), 100)
        self.assertEqual(readiness["sampling"]["segments"], len(groups["train"]))
        self.assertEqual(readiness["sampling"]["retained_iq_bytes"], sum(len(row["iq"]) * 8 for row in groups["train"]))
        self.assertEqual(groups["validation"], [])
        self.assertEqual(groups["test"], [])
        counts = collections.Counter(row["recording_group_id"] for row in groups["train"])
        self.assertEqual(set(counts), {entry["id"] for entry in entries})
        for entry, quota in zip(entries, quotas):
            self.assertEqual(counts[entry["id"]], quota)
            self.assertEqual(reads[entry["path"]], (0, quota * 1024))
            self.assertLessEqual(quota, min(64, entry["sample_count"] // 1024))
        self.assertEqual(reads[entries[0]["path"]], (0, 1024))

    def test_minimum_one_per_file_budget_and_short_file_errors_are_not_hidden(self):
        for entries, length, required in ((self.entries(300), 65536, "Kürzere Segmentlänge"),
                                          (self.entries(2, samples=922), 1024, "kürzer")):
            config = ml._validate_config(self.config(entries, segment_length=length))
            with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory), \
                 mock.patch("signalclassifier.sigmf.read_recording", side_effect=AssertionError("must fail before IQ reads")):
                readiness = ml.dataset_readiness(config)
                self.assertFalse(readiness["ready"])
                self.assertTrue(any(required in error for error in readiness["errors"]), readiness["errors"])
                self.assertFalse(readiness["sampling"]["all_recordings_represented"])
                with self.assertRaises(ValueError):
                    ml._recording_groups(config, length)

    def test_file_shortened_after_inventory_is_not_reported_as_represented(self):
        entries = self.entries(4, samples=1024)
        config = ml._validate_config(self.config(entries))
        def read(path, start=0, count=None):
            # Another file of this class remains valid, so a class-only check
            # would miss the lost recording and its falsely claimed coverage.
            length = 100 if path == entries[0]["path"] else count
            return np.ones(length, dtype=np.complex64), {"global": {"core:sample_rate": 256000}}
        with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory), \
             mock.patch("signalclassifier.sigmf.read_recording", side_effect=read):
            readiness = ml.dataset_readiness(config)
            self.assertTrue(readiness["ready"], readiness["errors"])
            self.assertTrue(readiness["sampling"]["all_recordings_represented"])
            with self.assertRaisesRegex(ValueError, "geplante Samplezahl"):
                ml._recording_groups(config, 1024)

    def test_existing_default_budget_policy_still_refuses_large_inputs(self):
        entries = self.entries(5)
        config = ml._validate_config(self.config(entries, segment_length=65536, use_all_recordings=False))
        with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory), \
             mock.patch.object(ml, "_recording_quota_plan", side_effect=AssertionError("default must not sample")):
            result = ml.dataset_readiness(config)
        self.assertFalse(result["ready"])
        self.assertNotIn("sampling", result)
        self.assertTrue(any("128-MiB-IQ-Budget" in error for error in result["errors"]))

    def test_short_file_can_use_a_smaller_complete_segment_and_every_class_survives(self):
        entries = self.entries(2, samples=922)
        config = ml._validate_config(self.config(entries, segment_length=512))
        with mock.patch.object(ml, "_recording_inventory", side_effect=self.inventory):
            result = ml.dataset_readiness(config)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["sampling"]["segments"], 2)
        self.assertEqual([row["usable_segments"] for row in result["classes"]], [1, 1])
        self.assertFalse(result["sampling"]["capped"])

    def test_report_and_model_save_sampling_and_remain_unvalidated_with_one_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root, entries = Path(directory), []
            for index in range(4):
                label = "AM" if index % 2 == 0 else "FM"
                writer = SigMFWriter(root / f"actual-{index}", 256000, 100e6,
                                     extra={"signalclassifier:group_id": "single-shared-origin"})
                writer.append(ml.generate_signal(label, 2048, seed=index))
                path = writer.close()
                entries.append({"id": f"actual-{index}", "path": str(path), "label": label, "verified": True})
            config = self.config(entries, segment_length=512, samples_per_class=12)
            report = ml.run_experiment(config, root / "run")
            model = ml.load_model_metadata(report["model_path"])
            self.assertEqual(report["recording_sampling"], report["readiness"]["sampling"])
            self.assertEqual(model["recording_sampling"], report["recording_sampling"])
            self.assertTrue(report["use_all_recordings"])
            self.assertTrue(model["use_all_recordings"])
            self.assertEqual(report["recording_sampling"]["recordings"], 4)
            self.assertTrue(report["recording_sampling"]["all_recordings_represented"])
            self.assertFalse(report["evaluation_available"])
            self.assertIsNone(report["metrics"]["accuracy"])
            self.assertIsNone(report["score"]["total"])
            self.assertEqual(report["trials"], [])
            self.assertEqual(report["final_test_evaluations"], 0)
            self.assertEqual(report["data_usage"]["train"]["segments"], report["recording_sampling"]["segments"])
            self.assertTrue(any("Rohproben" in text for text in report["limitations"]))


if __name__ == "__main__":
    unittest.main()
