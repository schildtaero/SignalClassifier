"""Local WebSocket transport and cheap REST fallback; no physical receiver."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from signalclassifier.app import create_app


def display(frame_id=1):
    return {"frame_id": frame_id, "spectrum_frame_id": f"session:100000000:256000:{frame_id}",
            "frequencies_hz": [99e6, 100e6, 101e6], "power_dbfs": [-70., -20., -70.],
            "timestamp": "2026-11-22T12:00:00+00:00", "analysis_duration_seconds": .01,
            "center_frequency_hz": 100e6, "sample_rate": 256000., "classification_enabled": False,
            "detections": [], "receiver": {"session_id": "session", "running": True,
                "classification_enabled": False, "config": {"classification_enabled": False}}}


class SpectrumSocketTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        self.runtime = self.app.state.runtime
        self.runtime.analysis.close()
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.directory.cleanup()

    def test_rest_frame_has_no_model_or_database_access(self):
        with patch.object(self.runtime.capture, "display_snapshot", return_value=display()), \
             patch.object(self.runtime.capture, "inference_segment", side_effect=AssertionError("no IQ copy")), \
             patch.object(self.runtime, "active_model_path", side_effect=AssertionError("no model resolution")), \
             patch.object(self.runtime, "compare_latest", side_effect=AssertionError("no inference")), \
             patch.object(self.runtime.store, "all", side_effect=AssertionError("no archives")):
            response = self.client.get("/api/spectrum/frame?bins=1024")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"type": "spectrum", "sequence": 1, "frame": display()})
        self.assertEqual(self.client.get("/api/spectrum/frame?bins=2048").status_code, 400)
        self.assertFalse(self.runtime.capture.status()["running"])

    def test_websocket_rejects_foreign_null_or_invalid_origin_before_subscribing(self):
        for origin in ("https://evil.invalid", "null", "", "file://testserver", "http://testserver:9000"):
            with self.subTest(origin=origin), self.assertRaises(WebSocketDisconnect) as failure:
                with self.client.websocket_connect("/api/spectrum/stream", headers={"origin": origin}):
                    self.fail("foreign origin accepted")
            self.assertEqual(failure.exception.code, 1008)
        self.assertEqual(self.runtime.spectrum_stream.stats()["clients"], 0)

    def test_query_limits_do_not_allocate_subscribers(self):
        for query in ("max_fps=0", "max_fps=31", "max_fps=nan", "max_fps=inf", "bins=8192", "bins=invalid"):
            with self.subTest(query=query), self.assertRaises(WebSocketDisconnect) as failure:
                with self.client.websocket_connect("/api/spectrum/stream?" + query):
                    self.fail("invalid query accepted")
            self.assertEqual(failure.exception.code, 1008)
        self.assertEqual(self.runtime.spectrum_stream.stats()["clients"], 0)

    def test_same_origin_and_cli_clients_get_shared_frames_without_starting_receiver(self):
        with patch.object(self.runtime.capture, "display_snapshot", return_value=display()), \
             patch.object(self.runtime.capture, "start", side_effect=AssertionError("no hardware start")), \
             patch("signalclassifier.app.ml.classify", side_effect=AssertionError("no inference")):
            for headers in ({"origin": "http://testserver"}, {}):
                with self.client.websocket_connect("/api/spectrum/stream", headers=headers) as socket:
                    message = socket.receive_json()
                    self.assertEqual(message["type"], "spectrum")
                    self.assertEqual(message["sequence"], 1)
                    self.assertFalse(message["frame"]["classification_enabled"])
        self.assertFalse(self.runtime.capture.status()["running"])

    def test_fifth_client_is_refused_and_disconnects_release_capacity(self):
        with patch.object(self.runtime.capture, "display_snapshot", return_value=display()):
            with ExitStack() as stack:
                for _ in range(4):
                    socket = stack.enter_context(self.client.websocket_connect("/api/spectrum/stream"))
                    self.assertEqual(socket.receive_json()["sequence"], 1)
                self.assertEqual(self.runtime.spectrum_stream.stats()["clients"], 4)
                with self.assertRaises(WebSocketDisconnect) as failure:
                    with self.client.websocket_connect("/api/spectrum/stream"):
                        self.fail("fifth client accepted")
                self.assertEqual(failure.exception.code, 1013)
            deadline = time.monotonic() + 2
            while self.runtime.spectrum_stream.stats()["clients"] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.runtime.spectrum_stream.stats()["clients"], 0)

    def test_synthetic_spectrum_arrives_continuously_without_rest_polling(self):
        response = self.client.post("/api/receiver/start", json={"source": "synthetic", "mode": "monitor",
            "classification_enabled": False, "sample_rate": 256000, "block_size": 4096,
            "duration_seconds": 1.2, "spectrum_refresh_hz": 15})
        self.assertEqual(response.status_code, 200, response.text)
        seen = []
        with patch.object(self.runtime.capture, "inference_segment", side_effect=AssertionError("no model IQ")), \
             self.client.websocket_connect("/api/spectrum/stream?max_fps=20") as socket:
            for _ in range(12):
                message = socket.receive_json()
                if message["type"] == "spectrum" and message["frame"].get("power_dbfs"):
                    seen.append(message)
                if len(seen) >= 4:
                    break
        self.assertGreaterEqual(len(seen), 4)
        self.assertEqual([m["sequence"] for m in seen], sorted({m["sequence"] for m in seen}))
        for message in seen:
            frame = message["frame"]
            self.assertLessEqual(len(frame["power_dbfs"]), 1024)
            self.assertEqual(len(frame["power_dbfs"]), len(frame["frequencies_hz"]))
            self.assertTrue(frame["synthetic"])
            self.assertFalse(frame["classification_enabled"])
            self.assertFalse(frame["rf_timestamp_verified"])
            self.assertNotIn("iq", frame)


if __name__ == "__main__":
    unittest.main()
