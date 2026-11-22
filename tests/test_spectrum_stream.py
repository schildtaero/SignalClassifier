import copy
import json
import math
import tempfile
import threading
import time
import unittest
from unittest import mock

from signalclassifier import spectrum_stream as stream


class DisplayCapture:
    """A display-only fake deliberately exposes no IQ/model/database APIs."""
    def __init__(self, frame=None):
        self.lock = threading.Lock()
        self.frame = frame
        self.calls = []

    def display_snapshot(self, *, max_bins, since_frame_id):
        with self.lock:
            self.calls.append((max_bins, since_frame_id))
            if isinstance(self.frame, Exception):
                raise self.frame
            if self.frame is None or ("frame_id" in self.frame and self.frame["frame_id"] == since_frame_id):
                return None
            return self.frame

    def set_frame(self, frame):
        with self.lock:
            self.frame = frame


def frame(revision=1):
    return {"frame_id": revision, "sequence": revision, "timestamp": "2026-11-22T12:00:00Z",
            "spectrum_frame_id": "immutable-fft-token", "frequency_hz": (100., 101.), "power_dbfs": [-40., -41.]}


class SpectrumHubTests(unittest.TestCase):
    def hub(self, capture, **kwargs):
        hub = stream.SpectrumHub(capture, **kwargs)
        self.addCleanup(hub.close)
        return hub

    def test_lazy_lifecycle_has_no_capture_calls_without_consumers(self):
        capture = DisplayCapture(frame(0)); hub = self.hub(capture)
        self.assertFalse(hub.closed)
        self.assertFalse(hub.stats()["running"])
        time.sleep(.025)
        self.assertEqual(capture.calls, [])
        token = hub.subscribe()
        sequence, packet = hub.wait_frame(token, -1)
        self.assertEqual(sequence, 0)
        self.assertEqual(json.loads(packet)["sequence"], 0)
        hub.unsubscribe(token)
        time.sleep(.06)  # Allow an in-flight display-only fetch to finish.
        count = len(capture.calls)
        time.sleep(.07)
        self.assertEqual(len(capture.calls), count)
        hub.close()
        self.assertTrue(hub.closed)
        self.assertFalse(hub.stats()["running"])
        self.assertIsNone(hub.wait_frame(token, -1, timeout=0))
        with self.assertRaises(ValueError):
            hub.subscribe()

    def test_clients_share_exactly_one_serialization_and_immutable_string(self):
        capture = DisplayCapture(frame(42)); hub = self.hub(capture)
        with mock.patch.object(stream, "_encode_packet", wraps=stream._encode_packet) as encode:
            first, second = hub.subscribe(), hub.subscribe()
            a = hub.wait_frame(first, -1)
            b = hub.wait_frame(second, -1)
            self.assertEqual(a[0], 42)
            self.assertIs(a[1], b[1])
            self.assertEqual(encode.call_count, 1)
        self.assertEqual(hub.stats()["published_frames"], 1)
        self.assertEqual(hub.stats()["latest_sequence"], 42)
        self.assertTrue(all(bins == 1024 for bins, _ in capture.calls))

    def test_slow_client_skips_old_frames_and_reconnect_uses_global_capture_revision(self):
        capture = DisplayCapture(frame(100)); hub = self.hub(capture)
        fast, slow = hub.subscribe(), hub.subscribe()
        hub.wait_frame(fast, -1)
        capture.set_frame(frame(104))
        self.assertEqual(hub.wait_frame(fast, 100)[0], 104)
        capture.set_frame(frame(110))
        self.assertEqual(hub.wait_frame(fast, 104)[0], 110)
        self.assertEqual(hub.wait_frame(slow, 100)[0], 110)
        hub.unsubscribe(slow)
        new = hub.subscribe()
        self.assertEqual(hub.wait_frame(new, -1)[0], 110)
        self.assertEqual(hub.stats()["published_frames"], 3)
        self.assertIsNone(hub.wait_frame(new, 110, timeout=.02))

    def test_input_snapshot_is_not_mutated_or_retained_by_reference(self):
        original = frame(1); original["nonfinite"] = [float("nan"), float("inf"), -float("inf")]
        before = copy.deepcopy(original)
        capture = DisplayCapture(original); hub = self.hub(capture)
        token = hub.subscribe(); _, packet = hub.wait_frame(token, -1)
        parsed = json.loads(packet)
        self.assertEqual(parsed["frame"]["nonfinite"], [None, None, None])
        self.assertTrue(math.isnan(original["nonfinite"][0]))
        self.assertEqual(original["frequency_hz"], before["frequency_hz"])
        self.assertEqual(parsed["frame"]["timestamp"], original["timestamp"])
        original["power_dbfs"][0] = -999
        self.assertEqual(json.loads(hub.wait_frame(token, -1)[1])["frame"]["power_dbfs"][0], -40)

    def test_status_changes_publish_even_when_fft_identity_stays_unchanged(self):
        capture = DisplayCapture(frame(1)); hub = self.hub(capture)
        token = hub.subscribe(); first = hub.wait_frame(token, -1)
        newer = frame(2); newer["receiver"] = {"state": "stopped"}
        capture.set_frame(newer)
        second = hub.wait_frame(token, first[0])
        self.assertEqual(second[0], 2)
        self.assertEqual(json.loads(first[1])["frame"]["spectrum_frame_id"], json.loads(second[1])["frame"]["spectrum_frame_id"])
        self.assertEqual(json.loads(second[1])["frame"]["receiver"]["state"], "stopped")

    def test_limit_unsubscribe_timeout_and_closed_waiters(self):
        capture = DisplayCapture(); hub = self.hub(capture, max_clients=1)
        token = hub.subscribe()
        with self.assertRaises(ValueError):
            hub.subscribe()
        self.assertIsNone(hub.wait_frame(token, -1, timeout=.02))
        returned = []
        thread = threading.Thread(target=lambda: returned.append(hub.wait_frame(token, -1, timeout=5)))
        thread.start(); hub.unsubscribe(token); thread.join(timeout=.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(returned, [None])
        self.assertIsNone(hub.wait_frame("unknown", -1, timeout=0))
        token = hub.subscribe()
        returned.clear()
        thread = threading.Thread(target=lambda: returned.append(hub.wait_frame(token, -1, timeout=5)))
        thread.start(); hub.close(); thread.join(timeout=.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(returned, [None])

    def test_errors_and_oversized_packets_are_bounded_and_recover(self):
        for invalid in (RuntimeError("private exception details"), {"frame_id": 1, "text": "x" * 300000},
                        {"frame_id": 1, "text": ["x" * 10000] * 30}, {"power": [1, 2]}):
            with self.subTest(invalid=type(invalid).__name__):
                capture = DisplayCapture(invalid); hub = self.hub(capture)
                token = hub.subscribe()
                self.assertIsNone(hub.wait_frame(token, -1, timeout=.08))
                stats = hub.stats()
                self.assertEqual(stats["published_frames"], 0)
                self.assertTrue(stats["last_error"])
                self.assertNotIn("private exception details", stats["last_error"])
                self.assertLessEqual(len(capture.calls), 2)
                capture.set_frame(frame(2))
                self.assertEqual(hub.wait_frame(token, -1, timeout=1)[0], 2)
                self.assertIsNone(hub.stats()["last_error"])
                hub.close()

    def test_duplicate_or_regressed_capture_revisions_cannot_increment_sequence(self):
        capture = DisplayCapture(frame(5)); hub = self.hub(capture)
        token = hub.subscribe(); hub.wait_frame(token, -1)
        capture.set_frame(frame(4))
        self.assertIsNone(hub.wait_frame(token, 5, timeout=.08))
        self.assertEqual(hub.stats()["latest_sequence"], 5)
        self.assertEqual(hub.stats()["published_frames"], 1)

    def test_constructor_and_wait_bounds(self):
        for kwargs in ({"max_clients": 0}, {"max_clients": True}, {"bins": 2048}, {"max_fps": float("nan")}, {"max_fps": 31}):
            with self.assertRaises(ValueError):
                stream.SpectrumHub(DisplayCapture(), **kwargs)
        hub = self.hub(DisplayCapture()); token = hub.subscribe()
        for after, timeout in ((-2, 1), (True, 1), (-1, -1), (-1, float("inf")), (-1, 31)):
            with self.assertRaises(ValueError):
                hub.wait_frame(token, after, timeout)

    def test_real_stopped_capture_delivers_initial_frame_zero_without_hardware(self):
        from signalclassifier.receivers import CaptureManager
        with tempfile.TemporaryDirectory() as directory:
            capture = CaptureManager(directory)
            with mock.patch.object(capture, "display_snapshot", wraps=capture.display_snapshot) as display:
                hub = self.hub(capture)
                token = hub.subscribe()
                sequence, encoded = hub.wait_frame(token, -1, timeout=1)
                self.assertEqual(sequence, 0)
                self.assertEqual(json.loads(encoded)["frame"]["frame_id"], 0)
                self.assertIsNone(display.call_args_list[0].kwargs["since_frame_id"])
                self.assertIsNone(hub.stats()["last_error"])
                hub.close()

    def test_reconnect_after_idle_validates_new_session_before_returning_a_packet(self):
        old = frame(10); old["receiver"] = {"session_id": "old-session"}
        capture = DisplayCapture(old); hub = self.hub(capture)
        first = hub.subscribe()
        self.assertEqual(hub.wait_frame(first, -1)[0], 10)
        hub.unsubscribe(first)
        newer = frame(20); newer["receiver"] = {"session_id": "new-session"}
        capture.set_frame(newer)
        count = len(capture.calls)
        reconnected = hub.subscribe()
        sequence, encoded = hub.wait_frame(reconnected, -1)
        self.assertEqual(sequence, 20)
        self.assertEqual(json.loads(encoded)["frame"]["receiver"]["session_id"], "new-session")
        self.assertIn((1024, None), capture.calls[count:])

    def test_reconnect_unchanged_revision_reuses_confirmed_packet_without_stalling(self):
        capture = DisplayCapture(frame(5)); hub = self.hub(capture)
        with mock.patch.object(stream, "_encode_packet", wraps=stream._encode_packet) as encode:
            first = hub.subscribe(); initial = hub.wait_frame(first, -1)
            hub.unsubscribe(first)
            count = len(capture.calls)
            reconnected = hub.subscribe(); refreshed = hub.wait_frame(reconnected, -1)
            self.assertEqual(refreshed[0], 5)
            self.assertIs(refreshed[1], initial[1])
            self.assertEqual(encode.call_count, 1)
            self.assertIn((1024, None), capture.calls[count:])


if __name__ == "__main__":
    unittest.main()
