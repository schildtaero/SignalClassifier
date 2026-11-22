import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from signalclassifier.analysis_runtime import AnalysisManager, SignalStore, normalize_settings
from signalclassifier.app import create_app
from signalclassifier.db import Store
from signalclassifier.ml import generate_signal
from signalclassifier.sigmf import write_recording


def entry(index=1, **changes):
    return {"channel_id": f"channel-{index}", "source_epoch": "session:0", "session_id": "session",
            "source": "synthetic", "origin_group_id": "origin", "observation_id": f"frame-{index}",
            "observed_at": "2026-11-21T12:00:00+00:00", "sample_rate": 25000.,
            "center_frequency_hz": 100e6 + index * 20000, "bandwidth_hz": 12500.,
            "iq": generate_signal("AM", 1024, sample_rate=25000), "sample_count": 1024,
            "source_sample_start": 100, "source_sample_stop": 4193, "source_sample_stride": 4,
            "parent_sample_rate": 100000., "quality": {"contiguous": True, "gap_free_verified": False}, **changes}


class FakeCapture:
    def __init__(self, entries):
        self.entries = entries
        self.ended = []

    def analysis_snapshots(self):
        return [dict(e, iq=e["iq"].copy()) for e in self.entries]

    def status(self):
        return {"source": "synthetic", "sample_rate": 256000., "center_frequency_hz": 100e6,
                "session_id": "session", "running": True}

    def snapshot(self):
        return {"receiver": self.status(), "timestamp": "2026-11-21T12:00:00+00:00",
                "detections": [], "analysis_status": {"unbuffered_detections_count": 0, "ended_channels": self.ended}}


def prediction(*args, models=None, **kwargs):
    name = models[0]["id"] if models else "unbekannt"
    return {"schema_version": 1, "status": "candidate" if models else "unknown", "label": name,
            "conclusions": {}, "stages": [{"id": "fusion", "parents": [], "status": "candidate",
            "decision": name, "reason": "deterministic injected test model"}],
            "routes": [{"model_id": m["id"], "model_hash": {"sha256": m["id"]}} for m in models or []]}


class AnalysisRuntimeTests(unittest.TestCase):
    def manager(self, directory, capture=None):
        def resolve(identifier):
            if identifier == "missing":
                raise ValueError("Modell fehlt")
            return {"id": identifier, "name": identifier, "model_path": str(Path(directory) / identifier)}
        return AnalysisManager(capture or FakeCapture([]), Store(Path(directory) / "library.sqlite3"),
                               directory, resolve, lambda: None, lambda f: [{"reason": str(f)}], start_worker=False)

    def test_settings_validate_paths_ranges_and_registry_before_mutating(self):
        for value in ({"model_path": "/tmp/forged"}, {"start_frequency_hz": 6e9},
                      {"experts": [{"model_id": "../bad"}]}, {"max_signals_per_cycle": True},
                      {"policy": {"min_margin": float("nan")}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_settings(value)
        with tempfile.TemporaryDirectory() as d:
            manager = self.manager(d)
            original = manager.get_settings()
            with self.assertRaises(ValueError):
                manager.set_settings({"experts": [{"model_id": "missing"}]})
            self.assertEqual(manager.get_settings(), original)
            self.assertEqual(len(normalize_settings({"experts": [{"model_id": f"m{i}"} for i in range(64)]})["experts"]), 64)

    def test_round_robin_processes_each_signal_and_deduplicates_frozen_windows(self):
        with tempfile.TemporaryDirectory() as d:
            manager = self.manager(d, FakeCapture([entry(i) for i in range(3)]))
            manager.set_settings({"max_signals_per_cycle": 1})
            with mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=prediction) as analyze:
                manager.process_snapshots()
                self.assertEqual(manager.signals.stats()["signal_count"], 3)
                self.assertEqual(manager.stats["pending_signals"], 2)
                manager.process_snapshots()
                manager.process_snapshots()
                manager.process_snapshots()
            self.assertEqual(analyze.call_count, 3)
            self.assertEqual(manager.signals.stats()["observation_count"], 3)
            self.assertTrue(all(s["status"] == "unknown" for s in manager.signals.list()))

    def test_receiver_switch_skips_all_automatic_analysis_and_scan_flush(self):
        with tempfile.TemporaryDirectory() as d:
            capture = FakeCapture([entry()])
            manager = self.manager(d, capture)
            disabled = {**capture.status(), "config": {"classification_enabled": False}}
            with mock.patch.object(capture, "status", return_value=disabled), \
                 mock.patch.object(capture, "snapshot", side_effect=AssertionError("no snapshot copy")), \
                 mock.patch.object(capture, "analysis_snapshots", side_effect=AssertionError("no IQ copies")), \
                 mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=AssertionError("no inference")):
                manager.process_snapshots()
                manager.finish_window()
                self.assertEqual(manager.overlay("session"), [])
                self.assertIn("deaktiviert", manager.stats["paused_reason"])
            self.assertEqual(manager.signals.stats()["observation_count"], 0)
            manager.process_snapshots()
            self.assertEqual(manager.signals.stats()["observation_count"], 1)
            self.assertIsNone(manager.stats["paused_reason"])

    def test_coverage_does_not_invent_unknown_receiver_limits_and_caps_analog_width(self):
        with tempfile.TemporaryDirectory() as d:
            capture = FakeCapture([])
            manager = self.manager(d, capture)
            self.assertIsNone(manager.coverage()["receiver_limits"]["max_frequency_hz"])
            with mock.patch.object(capture, "status", return_value={"source": "pluto", "sample_rate": 61.44e6,
                                    "center_frequency_hz": 1e9, "running": True}):
                coverage = manager.coverage()
            self.assertEqual(coverage["receiver_limits"]["max_frequency_hz"], 3.8e9)
            window = coverage["current_window"]
            self.assertEqual(window["stop_frequency_hz"] - window["start_frequency_hz"], 16e6)
            self.assertFalse(coverage["coverage_verified"])

    def test_scan_end_drains_all_retained_channel_windows(self):
        with tempfile.TemporaryDirectory() as d:
            manager = self.manager(d, FakeCapture([entry(i) for i in range(8)]))
            manager.set_settings({"max_signals_per_cycle": 1})
            with mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=prediction) as analyze:
                manager.finish_window()
            self.assertEqual(analyze.call_count, 8)
            self.assertEqual(manager.signals.stats()["observation_count"], 8)
            self.assertEqual(manager.stats["pending_signals"], 0)

    def test_unresolved_wideband_probe_remains_distinct_from_detection(self):
        with tempfile.TemporaryDirectory() as d:
            capture = FakeCapture([])
            capture.analysis_probe_snapshot = lambda: entry(observation_type="unresolved_window_probe")
            manager = self.manager(d, capture)
            manager.process_snapshots()
            result = manager.signals.list()[0]
            self.assertEqual(result["observation_type"], "unresolved_window_probe")
            self.assertEqual(result["decision"]["input"]["observation_type"], "unresolved_window_probe")
            self.assertTrue(any("keinem" in w or "kein einzelnes" in w for w in result["decision"]["warnings"]))

    def test_explicit_model_change_is_a_new_auditable_observation(self):
        with tempfile.TemporaryDirectory() as d:
            manager = self.manager(d)
            with mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=prediction):
                a = manager.analyze_entry(entry(), models=[{"id": "A", "model_path": str(Path(d) / "A")}])
                b = manager.analyze_entry(entry(), models=[{"id": "B", "model_path": str(Path(d) / "B")}])
                again = manager.analyze_entry(entry(), models=[{"id": "B", "model_path": str(Path(d) / "B")}])
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(b["decision"]["label"], "B")
            self.assertEqual(again["observation_count"], 2)
            history = manager.signals.history(a["id"])
            self.assertEqual([h["decision"]["label"] for h in history], ["B", "A"])
            self.assertEqual(b["source_context"]["source_sample_stop"], 4193)
            self.assertEqual(b["source_context"]["source_sample_stride"], 4)
            self.assertNotIn("iq", json.dumps(b))

    def test_model_artifact_revision_reruns_same_live_input(self):
        with tempfile.TemporaryDirectory() as d:
            model = Path(d) / "A"
            model.mkdir()
            (model / "model.json").write_text("first")
            manager = self.manager(d, FakeCapture([entry()]))
            manager.set_settings({"experts": [{"model_id": "A"}]})
            with mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=prediction) as analyze:
                manager.process_snapshots()
                manager.process_snapshots()
                (model / "model.json").write_text("second revision with new content")
                manager.process_snapshots()
            self.assertEqual(analyze.call_count, 2)
            self.assertEqual(manager.signals.stats()["observation_count"], 2)

    def test_changed_quality_and_provenance_update_same_iq_window(self):
        with tempfile.TemporaryDirectory() as d:
            capture = FakeCapture([entry()])
            manager = self.manager(d, capture)
            manager.process_snapshots()
            first = manager.signals.list()[0]
            self.assertEqual(first["decision"]["stages"][0]["status"], "passed")
            capture.entries[0]["quality"] = {"contiguous": False}
            manager.process_snapshots()
            rejected = manager.signals.get(first["id"])
            self.assertEqual(rejected["decision"]["stages"][0]["status"], "rejected")
            self.assertFalse(rejected["source_context"]["quality"]["contiguous"])
            capture.entries[0]["data_provenance"] = {"origin_kind": "synthetic", "rf_origin_verified": False}
            value = manager.analyze_entry(capture.entries[0], models=[])
            again = manager.analyze_entry(capture.entries[0], models=[])
            self.assertEqual(value["source_context"]["data_provenance"]["origin_kind"], "synthetic")
            self.assertEqual(again["observation_count"], 3)
            self.assertEqual(len(manager.signals.history(first["id"])), 3)

    def test_ended_unanalyzed_signal_no_longer_claims_to_be_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            capture = FakeCapture([entry(1), entry(2)])
            manager = self.manager(d, capture)
            manager.set_settings({"max_signals_per_cycle": 1})
            with mock.patch("signalclassifier.analysis_runtime.decision.analyze_signal", side_effect=prediction):
                manager.process_snapshots()
                waiting = next(s for s in manager.signals.list() if s["status"] == "not_run_budget")
                ended = {k: v for k, v in entry(2).items() if k != "iq"}
                capture.ended = [dict(ended, ended_at="2026-11-21T12:00:01+00:00", end_reason="no_longer_buffered")]
                capture.entries = []
                manager.process_snapshots()
            value = manager.signals.get(waiting["id"])
            self.assertEqual(value["status"], "insufficient_samples")
            self.assertEqual(value["activity"], "ended")

    def test_retention_uses_analysis_time_and_exposes_pruning(self):
        with tempfile.TemporaryDirectory() as d:
            store = SignalStore(Path(d) / "signals.sqlite3")
            for i in range(3):
                store.save({"id": str(i), "last_seen": "2026-01-01", "analysis_at": f"2026-11-21T12:00:0{i}"}, str(i), max_history=2, max_signals=2)
            store.save({"id": "old-recording", "last_seen": "2000-01-01", "analysis_at": "2026-11-21T12:01:00"}, "old", max_history=2, max_signals=2)
            self.assertIsNotNone(store.get("old-recording"))
            self.assertEqual(store.stats()["signal_count"], 2)
            self.assertGreater(store.stats()["history_pruned"], 0)
            self.assertGreater(store.stats()["signals_pruned"], 0)

    def test_store_and_source_provenance_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            manager = self.manager(d)
            value = manager.analyze_entry(entry(source="recording", synthetic=True,
                                               data_provenance={"origin_kind": "synthetic", "rf_origin_verified": False}))
            other = self.manager(d)
            loaded = other.signals.get(value["id"])
            self.assertTrue(loaded["synthetic"])
            self.assertFalse(loaded["source_context"]["data_provenance"]["rf_origin_verified"])
            self.assertEqual(loaded["decision"]["input"]["sample_sha256"], value["decision"]["input"]["sample_sha256"])
            self.assertNotIn("stages", other.snapshot()["signals"][0]["decision"])


class AnalysisApiTests(unittest.TestCase):
    def test_recording_pipeline_detail_and_export_are_persistent(self):
        with tempfile.TemporaryDirectory() as d, TestClient(create_app(Path(d) / "library")) as client:
            path = Path(write_recording(Path(d) / "fixture", generate_signal("AM", 2048), 256000, 125e6,
                                        extra={"sc:source": "recording", "sc:declared_source_synthetic": True}))
            response = client.post("/api/recordings/import", files={
                "metadata": (path.name, path.read_bytes(), "application/json"),
                "data": (path.with_suffix(".sigmf-data").name, path.with_suffix(".sigmf-data").read_bytes(), "application/octet-stream")})
            self.assertEqual(response.status_code, 200, response.text)
            recording_id = response.json()["id"]
            analyzed = client.post(f"/api/analysis/recordings/{recording_id}", json={})
            self.assertEqual(analyzed.status_code, 200, analyzed.text)
            value = analyzed.json()
            self.assertTrue(value["synthetic"])
            self.assertEqual(value["decision"]["status"], "unknown")
            self.assertEqual(len(value["decision"]["stages"]), 6)
            detail = client.get(f"/api/analysis/signals/{value['id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(len(detail.json()["history"]), 1)
            download = client.get(f"/api/analysis/signals/{value['id']}/download")
            self.assertIn("attachment", download.headers["content-disposition"])
            self.assertEqual(download.json()["source_context"]["source_sample_stop"], 2048)
            self.assertNotIn("iq", download.json())
            self.assertEqual(client.post(f"/api/analysis/recordings/{recording_id}", json={"model_path": "/forged"}).status_code, 400)

    def test_plan_exposes_unsupported_range_and_api_never_starts_hardware_for_preview(self):
        with tempfile.TemporaryDirectory() as d, TestClient(create_app(d)) as client:
            rt = client.app.state.runtime
            with mock.patch.object(rt.capture, "start", side_effect=AssertionError("preview must not open SDR")):
                response = client.post("/api/analysis/scan/plan", json={"source": "rsp1a", "sample_rate": 10e6})
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            self.assertFalse(plan["coverage_verified"])
            self.assertFalse(plan["simultaneous"])
            self.assertGreater(len(plan["uncovered_ranges"]), 0)
            self.assertLess(plan["coverage_fraction"], 1)
            self.assertEqual(client.post("/api/analysis/settings", json={"experts": [{"model_id": "missing"}]}).status_code, 404)
            self.assertEqual(client.post("/api/analysis/settings", json={"experts": [{"model_id": "x", "model_path": "/forged"}]}).status_code, 400)
            with mock.patch.object(rt.scanner, "status", return_value={"running": True}):
                self.assertEqual(client.post("/api/receiver/start", json={"source": "synthetic"}).status_code, 400)
                self.assertEqual(client.post("/api/receiver/stop").status_code, 400)


if __name__ == "__main__":
    unittest.main()
