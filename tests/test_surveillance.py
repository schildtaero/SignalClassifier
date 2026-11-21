import copy
import threading
import time
import unittest
from unittest import mock

from signalclassifier import surveillance
from signalclassifier.surveillance import ScanController, build_scan_plan


class ScanPlanTests(unittest.TestCase):
    def test_full_requested_band_exposes_receiver_limits_and_dc(self):
        for source, lower, upper in (("rsp1a", 1000., 2e9), ("pluto", 325e6, 3.8e9)):
            plan = build_scan_plan({"source": source, "sample_rate": 10e6})
            self.assertEqual(plan["requested_range"], {"start_frequency_hz": 0., "stop_frequency_hz": 6e9})
            self.assertEqual(plan["covered_ranges"], [{"start_frequency_hz": lower, "stop_frequency_hz": upper}])
            self.assertEqual(plan["uncovered_ranges"], [
                {"start_frequency_hz": 0., "stop_frequency_hz": lower},
                {"start_frequency_hz": upper, "stop_frequency_hz": 6e9}])
            self.assertAlmostEqual(plan["coverage_fraction"], (upper - lower) / 6e9)
            self.assertFalse(plan["coverage_verified"])
            self.assertFalse(plan["simultaneous"])
            self.assertTrue(all(lower <= step["center_frequency_hz"] <= upper for step in plan["steps"]))

    def test_tiles_have_no_gaps_and_respect_overlap(self):
        plan = build_scan_plan({"source": "rsp1a", "sample_rate": 1e6, "start_frequency_hz": 100e6,
                                "stop_frequency_hz": 103.7e6, "overlap_fraction": .2, "dwell_seconds": 2})
        self.assertEqual(plan["usable_bandwidth_hz"], 800000.)
        self.assertEqual(plan["step_count"], 6)
        self.assertEqual(plan["steps"][0]["start_frequency_hz"], 100e6)
        self.assertEqual(plan["steps"][-1]["stop_frequency_hz"], 103.7e6)
        for previous, current in zip(plan["steps"], plan["steps"][1:]):
            self.assertGreaterEqual(previous["stop_frequency_hz"] - current["start_frequency_hz"], .2 * 800000 - 1e-6)
        self.assertEqual(plan["uncovered_ranges"], [])
        self.assertEqual(plan["coverage_fraction"], 1.)
        self.assertEqual(plan["estimated_min_sweep_seconds"], 12)

    def test_narrow_and_edge_windows_do_not_invent_hardware_coverage(self):
        narrow = build_scan_plan({"source": "pluto", "sample_rate": 2e6, "start_frequency_hz": 324e6, "stop_frequency_hz": 325.1e6})
        self.assertEqual(narrow["step_count"], 1)
        self.assertEqual(narrow["covered_ranges"], [{"start_frequency_hz": 325e6, "stop_frequency_hz": 325.1e6}])
        self.assertEqual(narrow["uncovered_ranges"], [{"start_frequency_hz": 324e6, "stop_frequency_hz": 325e6}])
        unavailable = build_scan_plan({"source": "rsp1a", "start_frequency_hz": 2.4e9, "stop_frequency_hz": 2.5e9})
        self.assertEqual(unavailable["steps"], [])
        self.assertEqual(unavailable["coverage_fraction"], 0.)
        self.assertEqual(unavailable["uncovered_ranges"], [unavailable["requested_range"]])

    def test_pluto_analog_bandwidth_caps_usable_width_even_at_high_sample_rate(self):
        plan = build_scan_plan({"source": "pluto", "sample_rate": 61.44e6})
        self.assertEqual(plan["usable_bandwidth_hz"], 16e6)
        self.assertEqual(plan["receiver_limits"]["max_bandwidth_hz"], 20e6)

    def test_recording_only_covers_supplied_fixed_window(self):
        plan = build_scan_plan({"source": "recording", "start_frequency_hz": 99e6, "stop_frequency_hz": 101e6,
                                "sample_rate": 1e6, "receiver_config": {"center_frequency_hz": 100e6}})
        self.assertEqual(plan["step_count"], 1)
        self.assertEqual(plan["covered_ranges"], [{"start_frequency_hz": 99.6e6, "stop_frequency_hz": 100.4e6}])
        self.assertAlmostEqual(plan["coverage_fraction"], .4)
        with self.assertRaisesRegex(ValueError, "center_frequency_hz"):
            build_scan_plan({"source": "recording", "sample_rate": 1e6})
        with self.assertRaisesRegex(ValueError, "sample_rate"):
            build_scan_plan({"source": "recording", "receiver_config": {"center_frequency_hz": 100e6}})

    def test_step_budget_is_checked_before_allocating_a_large_scan(self):
        with self.assertRaisesRegex(ValueError, "überschreitet max_steps"):
            build_scan_plan({"source": "rsp1a", "sample_rate": 8000})
        with self.assertRaisesRegex(ValueError, "überschreitet max_steps=1"):
            build_scan_plan({"source": "rsp1a", "sample_rate": 1e6, "start_frequency_hz": 100e6,
                             "stop_frequency_hz": 102e6, "max_steps": 1})

    def test_invalid_values_are_rejected_without_mutating_input(self):
        config = {"source": "rsp1a", "receiver_config": {"gain_db": 25}}
        before = copy.deepcopy(config)
        build_scan_plan(config)
        self.assertEqual(config, before)
        for update in ({"start_frequency_hz": -1}, {"stop_frequency_hz": 6e9 + 1},
                       {"start_frequency_hz": 100, "stop_frequency_hz": 100}, {"sample_rate": float("nan")},
                       {"overlap_fraction": 1}, {"max_steps": 1.5}, {"max_steps": 4097},
                       {"repeat": "yes"}, {"source": "synthetic"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_scan_plan({**config, **update})


class FakeCapture:
    def __init__(self, *, hold=False, natural_complete=False):
        self.lock = threading.RLock()
        self.starts = []
        self.stops = []
        self.hold = hold
        self.natural_complete = natural_complete
        self.data = {"state": "stopped", "busy": False, "running": False, "session_id": "", "samples": 0}

    def start(self, config):
        with self.lock:
            if self.data.get("busy"):
                raise ValueError("already in use")
            self.starts.append(copy.deepcopy(config))
            self.data = {"state": "running", "busy": True, "running": True, "session_id": f"scan-{len(self.starts)}",
                         "sample_rate": config["sample_rate"], "center_frequency_hz": config["center_frequency_hz"],
                         "samples": 0 if self.hold else 800, "started_at": "2026-11-21T12:00:00Z", "config": config}
            return copy.deepcopy(self.data)

    def stop(self):
        with self.lock:
            self.stops.append(self.data["session_id"])
            self.data.update(state="stopped", busy=False, running=False, reason="user_stop")
            return copy.deepcopy(self.data)

    def status(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            if self.natural_complete and self.starts and result["session_id"].startswith("scan-"):
                self.data.update(state="stopped", busy=False, running=False, reason="duration_limit")
                result = copy.deepcopy(self.data)
            return result


class ScanControllerTests(unittest.TestCase):
    def config(self, **updates):
        return {"source": "rsp1a", "start_frequency_hz": 100e6, "stop_frequency_hz": 101e6,
                "sample_rate": 1e6, "dwell_seconds": .1, "receiver_config": {"gain_db": 22}, **updates}

    def wait(self, controller, *, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = controller.status()
            if not status["running"]:
                return status
            time.sleep(.005)
        self.fail("scan did not terminate within bounded test timeout")

    def test_scan_stops_each_owned_window_then_hands_off_before_next_start(self):
        capture = FakeCapture()
        callbacks = []
        def callback():
            self.assertFalse(capture.status()["busy"])
            callbacks.append((len(capture.starts), capture.status()["session_id"]))
            time.sleep(.01)
        controller = ScanController(capture, callback)
        self.addCleanup(controller.stop)
        controller.start(self.config())
        status = self.wait(controller)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(len(capture.starts), 2)
        self.assertEqual(capture.stops, ["scan-1", "scan-2"])
        self.assertEqual(callbacks, [(1, "scan-1"), (2, "scan-2")])
        self.assertEqual(status["completed_steps"], 2)
        self.assertEqual(status["completed_cycles"], 1)
        self.assertGreaterEqual(status["total_analysis_pause_seconds"], .02)
        self.assertEqual(len(status["observation_gaps"]), 1)
        self.assertGreaterEqual(status["observation_gaps"][0]["seconds"], .01)
        self.assertFalse(status["coverage_verified"])
        self.assertFalse(status["hardware_verified"])
        for config in capture.starts:
            self.assertEqual(config["duration_seconds"], .1)
            self.assertEqual(config["gain_db"], 22)
            self.assertEqual(config["mode"], "monitor")
            self.assertEqual(config["scan_id"], status["scan_id"])
            self.assertEqual(config["capture_group_id"], "scan-" + status["scan_id"])
        window = status["observed_windows"][0]
        self.assertEqual(window["sample_count"], 800)
        self.assertEqual(window["sample_duration_seconds"], .0008)
        self.assertEqual(window["requested_dwell_seconds"], .1)
        self.assertGreaterEqual(window["host_dwell_seconds"], .09)
        self.assertFalse(window["rf_timestamp_verified"])

    def test_busy_receiver_is_not_stopped(self):
        capture = FakeCapture()
        capture.data.update(state="running", running=True, busy=True, session_id="manual")
        controller = ScanController(capture)
        with self.assertRaisesRegex(ValueError, "belegt"):
            controller.start(self.config())
        controller.stop()
        self.assertEqual(capture.starts, [])
        self.assertEqual(capture.stops, [])

    def test_changed_session_is_not_stopped(self):
        capture = FakeCapture(hold=True)
        controller = ScanController(capture)
        self.addCleanup(controller.stop)
        controller.start(self.config(dwell_seconds=1))
        deadline = time.monotonic() + 1
        while not capture.starts and time.monotonic() < deadline:
            time.sleep(.005)
        with capture.lock:
            capture.data["session_id"] = "foreign"
        status = self.wait(controller)
        self.assertEqual(status["state"], "error")
        self.assertIn("fremde Sitzung", status["error"])
        self.assertEqual(capture.stops, [])

    def test_stop_cancels_dwell_without_starting_later_tiles(self):
        capture = FakeCapture(hold=True)
        controller = ScanController(capture)
        controller.start(self.config(dwell_seconds=10, repeat=True))
        deadline = time.monotonic() + 1
        while not capture.starts and time.monotonic() < deadline:
            time.sleep(.005)
        status = controller.stop()
        self.assertFalse(status["running"])
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(len(capture.starts), 1)
        self.assertEqual(capture.stops, ["scan-1"])
        self.assertEqual(status["stop_reason"], "user_stop")

    def test_stop_during_blocked_start_reports_stopping_until_owned_session_can_close(self):
        entered, release = threading.Event(), threading.Event()
        class SlowStart(FakeCapture):
            def start(self, config):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test start handoff timed out")
                return super().start(config)
        capture = SlowStart()
        controller = ScanController(capture)
        controller._join_seconds = .01
        self.addCleanup(controller.stop)
        self.addCleanup(release.set)
        controller.start(self.config())
        self.assertTrue(entered.wait(1))
        status = controller.stop()
        self.assertTrue(status["running"])
        self.assertEqual(status["state"], "stopping")
        self.assertEqual(capture.stops, [])
        with self.assertRaisesRegex(ValueError, "wird noch beendet"):
            controller.start(self.config())
        release.set()
        status = self.wait(controller)
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(capture.stops, ["scan-1"])
        self.assertEqual(len(capture.starts), 1)

    def test_driver_start_failure_does_not_stop_a_receiver_claimed_between_checks(self):
        class StartCollision(FakeCapture):
            def start(self, _config):
                with self.lock:
                    self.data.update(state="running", busy=True, running=True, session_id="foreign")
                raise ValueError("already in use")
        capture = StartCollision()
        controller = ScanController(capture)
        controller.start(self.config())
        status = self.wait(controller)
        self.assertEqual(status["state"], "error")
        self.assertIn("already in use", status["error"])
        self.assertEqual(capture.stops, [])
        self.assertEqual(capture.status()["session_id"], "foreign")

    def test_foreign_receiver_claimed_during_analysis_is_not_stopped_for_next_tile(self):
        capture = FakeCapture(natural_complete=True)
        def callback():
            with capture.lock:
                capture.data.update(state="running", busy=True, running=True, session_id="foreign")
        controller = ScanController(capture, callback)
        controller.start(self.config())
        status = self.wait(controller)
        self.assertEqual(status["state"], "error")
        self.assertIn("belegt", status["error"])
        self.assertEqual(capture.stops, [])
        self.assertEqual(len(capture.starts), 1)

    def test_failed_owned_stop_keeps_error_and_does_not_advance_scan(self):
        class StuckStop(FakeCapture):
            def stop(self):
                self.stops.append(self.data["session_id"])
                return self.status()
        capture = StuckStop()
        controller = ScanController(capture)
        controller.start(self.config())
        status = self.wait(controller)
        self.assertEqual(status["state"], "error")
        self.assertIn("reagiert nicht auf Stop", status["error"])
        self.assertEqual(len(capture.starts), 1)
        self.assertTrue(capture.status()["busy"])
        self.assertTrue(all(session == "scan-1" for session in capture.stops))

    def test_live_scan_rejects_recording_and_unsupported_ranges_without_hardware(self):
        capture = FakeCapture()
        controller = ScanController(capture)
        with self.assertRaisesRegex(ValueError, "Recording"):
            controller.start(self.config(source="recording", receiver_config={"center_frequency_hz": 100e6}))
        with self.assertRaisesRegex(ValueError, "Kein Scanfenster"):
            controller.start(self.config(start_frequency_hz=2.4e9, stop_frequency_hz=2.5e9))
        self.assertEqual(capture.starts, [])

    def test_natural_completion_does_not_stop_ended_capture_and_missing_samples_remain_unknown(self):
        capture = FakeCapture(hold=True, natural_complete=True)
        controller = ScanController(capture)
        controller.start(self.config(stop_frequency_hz=100.1e6))
        status = self.wait(controller)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(capture.stops, [])
        self.assertTrue(status["data_stale"])
        self.assertIsNone(status["sample_progress_age_seconds"])
        self.assertFalse(status["observed_windows"][0]["has_sample_evidence"])
        self.assertTrue(status["observed_windows"][0]["data_stale"])

    def test_sample_progress_becomes_stale_and_status_is_a_copy(self):
        capture = FakeCapture(natural_complete=True)
        controller = ScanController(capture)
        controller.start(self.config(stop_frequency_hz=100.1e6))
        status = self.wait(controller)
        self.assertFalse(status["data_stale"])
        self.assertEqual(status["observed_windows"][0]["sample_count"], 800)
        status["plan"]["steps"].clear()
        with mock.patch.object(surveillance.time, "monotonic", return_value=time.monotonic() + 10):
            stale = controller.status()
        self.assertTrue(stale["data_stale"])
        self.assertTrue(stale["observed_windows"][0]["data_stale"])
        self.assertEqual(stale["plan"]["step_count"], 1)
        self.assertEqual(len(stale["plan"]["steps"]), 1)

    def test_callback_failure_is_visible_and_does_not_restart_receiver(self):
        capture = FakeCapture(natural_complete=True)
        def callback():
            raise ValueError("analysis failed")
        controller = ScanController(capture, callback)
        controller.start(self.config())
        status = self.wait(controller)
        self.assertEqual(status["state"], "error")
        self.assertIn("analysis failed", status["error"])
        self.assertEqual(status["observed_windows"][0]["analysis_error"], "analysis failed")
        self.assertEqual(len(capture.starts), 1)

    def test_repeated_scan_keeps_bounded_history(self):
        capture = FakeCapture(natural_complete=True)
        controller = None
        def callback():
            if len(capture.starts) >= 135:
                controller.stop()
        controller = ScanController(capture, callback)
        self.addCleanup(controller.stop)
        controller.start(self.config(stop_frequency_hz=100.1e6, repeat=True))
        status = self.wait(controller)
        self.assertEqual(status["completed_steps"], 135)
        self.assertEqual(len(status["observed_windows"]), 128)
        self.assertEqual(status["history_dropped_windows"], 7)
        self.assertLessEqual(len(status["observation_gaps"]), 128)
        self.assertEqual(status["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
