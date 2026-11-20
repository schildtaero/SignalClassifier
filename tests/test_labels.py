"""Provisional band labels must describe the complete recording, not just its centre."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from signalclassifier.db import Store
from signalclassifier import labels


class RecordingSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "labels.sqlite3")
        labels.seed(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def recording(self, center, rate=256000, **extra):
        return {"center_frequency_hz": center, "sample_rate": rate,
                "metadata": {"global": {"core:sample_rate": rate},
                             "captures": [{"core:sample_start": 0, "core:frequency": center}]}, **extra}

    def test_initial_am_fm_labels_use_metadata_even_without_classification_priors(self):
        for frequency, expected, rate in ((125e6, "AM", 5e6), (100e6, "FM", 2e6)):
            with self.subTest(expected=expected):
                recording = self.recording(frequency, rate, use_priors=False, mode="wideband")
                suggestion = labels.recording_suggestions(self.store, recording)["label_suggestion"]
                self.assertEqual(suggestion["label"], expected)
                self.assertFalse(suggestion["ambiguous"])
                self.assertTrue(suggestion["requires_confirmation"])
                self.assertEqual(suggestion["label_status"], "suggested")
                self.assertNotIn("verified", suggestion)

    def test_existing_human_label_and_emitter_database_are_not_modified(self):
        recording = self.recording(125e6, annotation={"label": "FM", "verified": True, "notes": "human"})
        before = copy.deepcopy(recording)
        entries_before = self.store.all("emitter")
        with mock.patch.object(self.store, "put", side_effect=AssertionError("Suggestions must not write labels")):
            result = labels.recording_suggestions(self.store, recording)
        self.assertEqual(result["label_suggestion"]["label"], "AM")
        self.assertEqual(recording, before)
        self.assertEqual(self.store.all("emitter"), entries_before)

    def test_full_bandwidth_crossing_services_blocks_center_only_default(self):
        result = labels.recording_suggestions(self.store, self.recording(100e6, 60e6))
        self.assertIsNone(result["label_suggestion"])
        classes = {item["label"] for item in result["label_suggestions"]}
        self.assertTrue({"AM", "FM"}.issubset(classes))
        self.assertTrue(all(item["ambiguous"] for item in result["label_suggestions"]))

    def test_partial_band_edge_coverage_is_not_one_global_label(self):
        result = labels.recording_suggestions(self.store, self.recording(107.99e6, 256000))
        self.assertIsNone(result["label_suggestion"])
        self.assertEqual([item["label"] for item in result["label_suggestions"]], ["FM"])
        self.assertTrue(result["label_suggestions"][0]["ambiguous"])
        self.assertIn("gesamte Aufnahmefenster", result["label_suggestion_reason"])

    def test_airband_data_overlap_and_shared_433_band_remain_ambiguous(self):
        for frequency, required in ((131.5e6, {"AM", "ACARS"}), (433.5e6, {"LoRa", "OOK", "FSK", "DMR"})):
            with self.subTest(frequency=frequency):
                result = labels.recording_suggestions(self.store, self.recording(frequency, 25000))
                self.assertIsNone(result["label_suggestion"])
                self.assertTrue(required.issubset({item["label"] for item in result["label_suggestions"]}))
                self.assertTrue(all(item["ambiguous"] for item in result["label_suggestions"]))

    def test_capture_retunes_block_global_labels_even_with_same_modulation(self):
        for second in (126e6, 100e6):
            with self.subTest(second=second):
                recording = self.recording(125e6)
                recording["metadata"]["captures"].append({"core:sample_start": 4096, "core:frequency": second})
                result = labels.recording_suggestions(self.store, recording)
                self.assertIsNone(result["label_suggestion"])
                self.assertIn("mehrere Abstimmfrequenzen", result["label_suggestion_reason"])
                self.assertTrue(all(item["ambiguous"] for item in result["label_suggestions"]))

    def test_repeated_same_frequency_captures_and_raw_sigmf_are_supported(self):
        recording = self.recording(125e6)
        recording["metadata"]["captures"].append({"core:sample_start": 4096, "core:frequency": 125e6})
        self.assertEqual(labels.recording_suggestions(self.store, recording["metadata"])["label_suggestion"]["label"], "AM")

    def test_missing_malformed_or_unlocated_metadata_produce_no_label(self):
        invalid = [None, {}, {"center_frequency_hz": 0, "sample_rate": 250000},
                   {"center_frequency_hz": 125e6}, {"center_frequency_hz": 125e6, "sample_rate": float("nan")},
                   {"center_frequency_hz": 125e6, "sample_rate": -1},
                   {"center_frequency_hz": True, "sample_rate": 250000},
                   {"center_frequency_hz": 10**1000, "sample_rate": 250000},
                   {"metadata": {"global": [], "captures": []}},
                   self.recording(125e6, metadata={"global": {"core:sample_rate": 250000}, "captures": []}),
                   self.recording(125e6, metadata={"global": {"core:sample_rate": 250000}, "captures": [{"core:sample_start": 0}]}),
                   self.recording(125e6, error="corrupt metadata")]
        for recording in invalid:
            with self.subTest(recording=str(recording)[:100]):
                result = labels.recording_suggestions(self.store, recording)
                self.assertIsNone(result["label_suggestion"])
                self.assertEqual(result["label_suggestions"], [])
                self.assertTrue(result["label_suggestion_reason"])

    def test_energy_clip_uses_actual_rf_window_and_snapshot_avoids_extra_queries(self):
        snapshot = self.store.all("emitter")
        recording = self.recording(125e6, 25000, mode="energy")
        recording["metadata"]["global"].update({"sc:original_center_frequency_hz": 100e6,
                                               "sc:parent_sample_rate": 10e6})
        with mock.patch.object(self.store, "all", side_effect=AssertionError("Use the provided emitter snapshot")):
            result = labels.recording_suggestions(self.store, recording, entries=snapshot)
        self.assertEqual(result["label_suggestion"]["label"], "AM")

    def test_custom_wfm_and_nfm_collapse_to_fm_and_region_filters_foreign_hints(self):
        for modulation in ("WFM", "NFM"):
            with self.subTest(modulation=modulation):
                entries = [{"id": "local", "name": "Custom FM", "standard": "unbekannt", "modulation": modulation,
                            "frequency_min_hz": 300e6, "frequency_max_hz": 301e6, "region": "DE-BY",
                            "confidence": .7, "source": "local settings"},
                           {"id": "foreign", "name": "Other allocation", "standard": "unbekannt", "modulation": "AM",
                            "frequency_min_hz": 300e6, "frequency_max_hz": 301e6, "region": "US", "confidence": .9}]
                result = labels.recording_suggestions(self.store, self.recording(300.5e6, 25000, region="DE-BY · Bamberg"), entries=entries)
                self.assertEqual(result["label_suggestion"]["label"], "FM")
                self.assertEqual(result["label_suggestion"]["emitter_ids"], ["local"])

    def test_partially_known_custom_modulation_does_not_hide_unknown_alternative(self):
        entry = {"id": "mixed", "modulation": "AM / unclassified", "frequency_min_hz": 300e6,
                 "frequency_max_hz": 301e6, "confidence": .8}
        result = labels.recording_suggestions(self.store, self.recording(300.5e6, 25000), entries=[entry])
        self.assertIsNone(result["label_suggestion"])
        self.assertEqual(result["label_suggestions"][0]["label"], "AM")
        self.assertTrue(result["label_suggestions"][0]["ambiguous"])

    def test_existing_frequency_lookup_keeps_full_emitter_contract(self):
        result = labels.suggest(self.store, 125e6)
        self.assertEqual(result[0]["id"], "airband")
        self.assertIn("frequency_min_hz", result[0])
        self.assertIn("modulation", result[0])
        self.assertTrue(result[0]["requires_confirmation"])
        self.assertFalse(result[0]["verified"])


if __name__ == "__main__":
    unittest.main()
