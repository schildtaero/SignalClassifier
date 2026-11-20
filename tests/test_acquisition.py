import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy import signal
import threading
from datetime import datetime

from signalclassifier.receivers import CaptureManager, _CaptureFailure, _Channelizer, spectrum_and_detections, validate_config
from signalclassifier.sigmf import SigMFWriter, read_metadata, read_recording, recording_info, write_recording


class SigMFTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_float_roundtrip_and_bounded_segment(self):
        iq = np.array([0.1 + 0.4j, -0.3j, -1 + 0.2j], np.complex64)
        path = write_recording(self.root / "test", iq, 48000, 118e6,
                               annotations=[{"core:sample_start": 0, "core:sample_count": 3, "core:label": "AM"}])
        loaded, meta = read_recording(path, start=1, count=1)
        np.testing.assert_array_equal(loaded, iq[1:2])
        self.assertEqual(meta["annotations"][0]["core:label"], "AM")
        self.assertEqual(recording_info(path)["samples"], 3)
        self.assertEqual(recording_info(path)["center_frequency_hz"], 118e6)

    def test_int16_is_little_endian_iq_and_normalized(self):
        path = write_recording(self.root / "integer", np.ones(2, np.complex64), 8000, 433e6)
        meta = json.loads(path.read_text())
        meta["global"]["core:datatype"] = "ci16_le"
        path.write_text(json.dumps(meta))
        np.array([32767, -32768, 16384, 0], dtype="<i2").tofile(path.with_suffix(".sigmf-data"))
        iq, _ = read_recording(path)
        np.testing.assert_allclose(iq, [32767 / 32768 - 1j, 0.5 + 0j])

    def test_reject_path_traversal_multichannel_and_partial_samples(self):
        path = write_recording(self.root / "test", np.ones(4, np.complex64), 8000, 433e6)
        meta = json.loads(path.read_text())
        for field, value in [("core:dataset", "../elsewhere.sigmf-data"), ("core:num_channels", 2)]:
            changed = json.loads(json.dumps(meta))
            changed["global"][field] = value
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                read_recording(path)
        path.write_text(json.dumps(meta))
        with path.with_suffix(".sigmf-data").open("ab") as handle:
            handle.write(b"x")
        with self.assertRaises(ValueError):
            read_metadata(path)

    def test_nonfinite_iq_is_rejected(self):
        with self.assertRaises(ValueError):
            write_recording(self.root / "bad", np.array([complex(float("nan"), 0)]), 8000, 433e6)


class SpectrumAndChannelTests(unittest.TestCase):
    def test_fast_decimated_fft_matches_full_rate_fir_reference(self):
        rng = np.random.default_rng(681)
        iq = (rng.standard_normal(16387) + 1j * rng.standard_normal(16387)).astype(np.complex64)
        for bandwidth in (150000, 23000, 1000):
            with self.subTest(bandwidth=bandwidth):
                extractor = _Channelizer(200000, 43123.5, bandwidth)
                pieces = [iq[:7001], iq[7001:12003], iq[12003:]]
                actual = np.concatenate([extractor.process(piece) for piece in pieces])
                mixed = iq * np.exp(-2j * np.pi * 43123.5 / 200000 * np.arange(len(iq)))
                expected = signal.fftconvolve(mixed, extractor.taps, mode="full")[:len(iq)][::extractor.decimation]
                np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=2e-5)

    def test_spectrum_tone_reference_and_frequency(self):
        rate = 256000
        t = np.arange(16384) / rate
        rng = np.random.default_rng(12)
        iq = np.exp(2j * np.pi * 32000 * t) + 1e-4 * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
        result = spectrum_and_detections(iq, rate, 100e6)
        detection = result["detections"][0]
        self.assertAlmostEqual(max(result["power_dbfs"]), 0, places=2)
        self.assertLess(abs(detection["frequency_hz"] - (100e6 + 32000)), rate / 2048)
        self.assertIsNone(detection["snr_db"])

    def test_channelizer_preserves_phase_across_arbitrary_buffer_boundaries(self):
        rate = 200000
        t = np.arange(32768) / rate
        iq = (np.exp(2j * np.pi * 41000 * t) + 0.3 * np.exp(2j * np.pi * -60000 * t)).astype(np.complex64)
        whole = _Channelizer(rate, 40000, 5000).process(iq)
        streamed = _Channelizer(rate, 40000, 5000)
        segments = [iq[:7001], iq[7001:18003], iq[18003:]]
        assembled = np.concatenate([streamed.process(block) for block in segments])
        np.testing.assert_allclose(assembled, whole, atol=5e-6, rtol=5e-6)
        # Desired +1 kHz component is retained after mixing; other emitter is rejected.
        settled = whole[100:]
        self.assertGreater(float(np.mean(np.abs(settled))), 0.95)
        output_rate = streamed.output_rate
        phase_frequency = np.angle(np.mean(settled[1:] * settled[:-1].conj())) * output_rate / (2 * np.pi)
        self.assertAlmostEqual(phase_frequency, 1000, delta=2)


class CaptureTests(unittest.TestCase):
    class ArraySource:
        center_frequency = 433e6
        paced = False

        def __init__(self, iq, rate):
            self.iq, self.sample_rate, self.position = iq, rate, 0

        def read(self, count):
            block = self.iq[self.position:self.position + count].copy()
            self.position += len(block)
            return block

        def close(self):
            pass

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = CaptureManager(self.directory.name)
        self.addCleanup(self.manager.stop)

    def finish(self, timeout=3):
        deadline = time.monotonic() + timeout
        while self.manager.status()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.manager.status()["running"], "capture did not finish")

    class FakeHardware:
        sample_rate = 100000.0
        center_frequency = 433e6
        paced = False
        owns_read_buffers = False

        def __init__(self, blocks=6, delay=0.001, failure=None):
            self.blocks = blocks
            self.delay = delay
            self.failure = failure
            self.index = 0
            self.buffer = np.empty(4096, np.complex64)
            self.closed = False

        def read(self, count):
            if self.index == self.blocks:
                if self.failure:
                    raise self.failure
                return np.empty(0, np.complex64)
            time.sleep(self.delay)
            self.index += 1
            self.buffer[:] = self.index / 100
            return self.buffer

        def close(self):
            self.closed = True

    def start_hardware(self, source, **config):
        with patch("signalclassifier.receivers._RSP1A", return_value=source):
            self.manager.start({"source": "rsp1a", "mode": "wideband", "sample_rate": 100000,
                                "center_frequency_hz": 433e6, "block_size": 4096,
                                "duration_seconds": 10, **config})

    def test_hardware_queue_owns_borrowed_buffers_and_preserves_order(self):
        source = self.FakeHardware(blocks=6)
        original = SigMFWriter.append

        def slow_write(writer, iq):
            time.sleep(0.01)
            return original(writer, iq)

        with patch.object(SigMFWriter, "append", slow_write):
            self.start_hardware(source, queue_capacity_bytes=8 * 4096 * 8)
            self.finish()
        status = self.manager.status()
        self.assertEqual(status["state"], "completed")
        self.assertTrue(source.closed)
        performance = status["performance"]
        self.assertTrue(performance["reader_queue_enabled"])
        self.assertEqual(performance["read_blocks"], 6)
        self.assertEqual(performance["processed_blocks"], 6)
        self.assertGreater(performance["queue_high_water_blocks"], 0)
        self.assertEqual(performance["gaps"], 0)
        self.assertFalse(performance["gap_free_verified"])
        self.assertGreaterEqual(performance["writer_ms"], 5)
        self.assertGreater(performance["processing_rtf"], 0)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["block_sample_count"], 4096)
        self.assertAlmostEqual(snapshot["block_duration_seconds"], 4096 / 100000)
        self.assertEqual(snapshot["timestamp_source"], "host_receive_end_utc")
        self.assertFalse(snapshot["rf_timestamp_verified"])
        self.assertGreater(snapshot["classification_channel"]["bandwidth_hz"], 0)
        recording = self.manager.list_recordings()[0]
        iq, metadata = read_recording(recording["metadata_path"])
        expected = np.repeat(np.arange(1, 7, dtype=np.float32) / 100, 4096).astype(np.complex64)
        np.testing.assert_array_equal(iq, expected)
        self.assertEqual(metadata["global"]["sc:integrity"]["reported_gaps"], 0)

    def test_queue_overflow_is_visible_and_saves_only_admitted_continuous_prefix(self):
        source = self.FakeHardware(blocks=30, delay=0.003)
        entered, release = threading.Event(), threading.Event()
        original = SigMFWriter.append

        def blocked_write(writer, iq):
            entered.set()
            release.wait(timeout=2)
            return original(writer, iq)

        with patch.object(SigMFWriter, "append", blocked_write):
            self.start_hardware(source, queue_capacity_bytes=2 * 4096 * 8)
            self.assertTrue(entered.wait(timeout=2))
            deadline = time.monotonic() + 2
            while self.manager.status()["performance"]["overflows"] == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            release.set()
            self.finish()
        status = self.manager.status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["reason"], "queue_overflow")
        performance = status["performance"]
        self.assertEqual(performance["overflows"], 1)
        self.assertEqual(performance["gaps"], 1)
        self.assertEqual(performance["queue_high_water_blocks"], 2)
        self.assertLessEqual(performance["queue_high_water_bytes"], performance["queue_capacity_bytes"])
        self.assertEqual(performance["queue_blocks"], 0)
        self.assertIsNotNone(performance["capture_error"])
        recording = self.manager.list_recordings()[0]
        iq, metadata = read_recording(recording["metadata_path"])
        blocks = len(iq) // 4096
        self.assertEqual(blocks, 3)
        expected = np.repeat(np.arange(1, blocks + 1, dtype=np.float32) / 100, 4096).astype(np.complex64)
        np.testing.assert_array_equal(iq, expected)
        integrity = metadata["global"]["sc:integrity"]
        self.assertEqual(integrity["reported_gaps"], 1)
        self.assertEqual(integrity["stop_reason"], "queue_overflow")
        self.assertEqual(integrity["source_samples_processed"], len(iq))
        self.assertGreater(integrity["source_samples_read"], len(iq))

    def test_reported_hardware_overflow_survives_in_recording_integrity(self):
        source = self.FakeHardware(blocks=2, failure=_CaptureFailure("SDR overflow", reason="receiver_overflow",
                                                                  reported_gap=True, overflow=True))
        self.start_hardware(source)
        self.finish()
        status = self.manager.status()
        self.assertEqual(status["reason"], "receiver_overflow")
        self.assertEqual(status["performance"]["gaps"], 1)
        recording = self.manager.list_recordings()[0]
        self.assertEqual(recording["samples"], 8192)
        integrity = recording["metadata"]["global"]["sc:integrity"]
        self.assertEqual(integrity["overflows"], 1)
        self.assertEqual(integrity["capture_error"], "SDR overflow")

    def test_write_failure_stops_stream_and_persists_error_with_numeric_prefix(self):
        source = self.FakeHardware(blocks=6)
        original = SigMFWriter.append
        writes = 0

        def failing_write(writer, iq):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("disk full")
            return original(writer, iq)

        with patch.object(SigMFWriter, "append", failing_write):
            self.start_hardware(source)
            self.finish()
        self.assertEqual(self.manager.status()["reason"], "writer_error")
        self.assertIn("disk full", self.manager.status()["error"])
        recording = self.manager.list_recordings()[0]
        self.assertEqual(recording["samples"], 4096)
        self.assertEqual(recording["metadata"]["global"]["sc:integrity"]["stop_reason"], "writer_error")

    def test_gate_close_metadata_includes_current_stored_block(self):
        rng = np.random.default_rng(93)
        iq = (0.001 * (rng.standard_normal(80000) + 1j * rng.standard_normal(80000))).astype(np.complex64)
        iq[:10000] += np.exp(2j * np.pi * .125 * np.arange(10000)).astype(np.complex64)
        with patch("signalclassifier.receivers._Synthetic", return_value=self.ArraySource(iq, 100000)):
            self.manager.start({"source": "synthetic", "mode": "energy", "sample_rate": 100000,
                                "block_size": 4096, "duration_seconds": 2})
            self.finish()
        closed = [r for r in self.manager.list_recordings()
                  if r["metadata"]["global"]["sc:stop_reason"] == "energy_gate_closed"]
        self.assertTrue(closed)
        for recording in closed:
            meta = recording["metadata"]["global"]
            self.assertGreaterEqual(meta["sc:integrity"]["source_samples_processed"], meta["sc:source_sample_stop"])
            self.assertEqual(meta["sc:performance"]["source_samples_processed"], meta["sc:integrity"]["source_samples_processed"])
            self.assertGreaterEqual(meta["sc:integrity"]["source_samples_read"], meta["sc:integrity"]["source_samples_processed"])

    def test_waterfall_timestamp_and_span_exclude_unanalyzed_rf_tail(self):
        rate, count = 5_000_000, 131072
        iq = np.exp(2j * np.pi * .125 * np.arange(count)).astype(np.complex64)
        with patch("signalclassifier.receivers._Synthetic", return_value=self.ArraySource(iq, rate)):
            self.manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": rate,
                                "block_size": count, "duration_seconds": 1})
            self.finish()
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["block_sample_count"], count)
        self.assertEqual(snapshot["analysis_sample_count"], 65536)
        self.assertEqual(snapshot["analysis_sample_offset"], 0)
        self.assertAlmostEqual(snapshot["analysis_duration_seconds"], 65536 / rate)
        self.assertAlmostEqual(snapshot["block_duration_seconds"], count / rate)
        self.assertEqual(snapshot["timestamp"], snapshot["analysis_timestamp"])
        self.assertEqual(snapshot["timestamp_source"], "host_receive_end_minus_unanalyzed_tail")
        received = datetime.fromisoformat(snapshot["block_received_at"])
        analyzed = datetime.fromisoformat(snapshot["analysis_timestamp"])
        self.assertAlmostEqual((received - analyzed).total_seconds(), 65536 / rate, places=5)
        self.assertFalse(snapshot["rf_timestamp_verified"])

    def test_wideband_capture_honors_storage_limit_and_metadata(self):
        self.manager.start({"source": "synthetic", "mode": "wideband", "sample_rate": 100000,
                            "duration_seconds": 1, "block_size": 4096, "max_bytes": 8000})
        self.finish()
        recordings = self.manager.list_recordings()
        self.assertEqual(len(recordings), 1)
        recording = recordings[0]
        self.assertEqual(recording["size_bytes"], 8000)
        self.assertEqual(recording["samples"], 1000)
        self.assertTrue(recording["synthetic"])
        self.assertEqual(recording["metadata"]["global"]["sc:stop_reason"], "storage_limit")
        self.assertEqual(recording["group_id"], self.manager.status()["session_id"])

    def test_replay_reads_exact_samples_and_finishes_at_eof(self):
        iq = np.exp(2j * np.pi * np.arange(6000) * 0.12).astype(np.complex64)
        path = write_recording(Path(self.directory.name) / "input", iq, 100000, 145e6)
        self.manager.start({"source": "recording", "recording_path": str(path), "duration_seconds": 1,
                            "block_size": 4096, "mode": "monitor"})
        self.finish()
        self.assertEqual(self.manager.status()["reason"], "end_of_recording")
        self.assertEqual(self.manager.status()["samples"], 6000)
        self.assertEqual(len(self.manager.list_recordings()), 0)
        self.assertEqual(self.manager.snapshot()["source"], "recording")

    def test_energy_capture_is_channelized_and_keeps_original_source_indices(self):
        rate = 100000
        rng = np.random.default_rng(12)
        iq = (0.001 * (rng.standard_normal(30000) + 1j * rng.standard_normal(30000))).astype(np.complex64)
        t = np.arange(16000) / rate
        iq[5000:21000] += np.exp(2j * np.pi * 14000 * t)
        path = write_recording(Path(self.directory.name) / "input", iq, rate, 433e6)
        self.manager.start({"source": "recording", "recording_path": str(path), "duration_seconds": 1,
                            "block_size": 4096, "mode": "energy"})
        self.finish()
        recordings = self.manager.list_recordings()
        self.assertGreaterEqual(len(recordings), 1)
        self.assertLess(sum(r["size_bytes"] for r in recordings), iq.nbytes)
        for recording in recordings:
            global_meta = recording["metadata"]["global"]
            self.assertLess(recording["sample_rate"], rate)
            self.assertEqual(global_meta["sc:parent_sample_rate"], rate)
            self.assertIn("sc:source_sample_start", global_meta)
            self.assertEqual(global_meta["sc:pretrigger_seconds"], 0)
            self.assertEqual(global_meta["sc:parent_recording_id"], "input")
            self.assertEqual(recording["group_id"], "input")
            channel, _ = read_recording(recording["metadata_path"])
            self.assertTrue(np.isfinite(channel).all())

    def test_live_driver_failure_is_visible_and_never_starts_synthetic(self):
        with patch("signalclassifier.receivers.importlib.import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(RuntimeError, "Pluto-Treiber fehlt"):
                self.manager.start({"source": "pluto", "center_frequency_hz": 433e6})
        status = self.manager.status()
        self.assertEqual(status["state"], "error")
        self.assertFalse(status["synthetic"])
        self.assertEqual(status["source"], "pluto")

    def test_pluto_stock_frequency_rejects_airband_and_nan_config(self):
        with self.assertRaisesRegex(ValueError, "325 MHz"):
            validate_config({"source": "pluto", "center_frequency_hz": 125e6})
        with self.assertRaises(ValueError):
            validate_config({"source": "synthetic", "threshold_db": float("nan")})

    def test_inference_ring_accumulates_model_windows_and_resets_discontinuous_contexts(self):
        manager = self.manager

        class ScriptedReceiver:
            sample_rate = 100000.0
            center_frequency = 100e6
            paced = False

            def __init__(self, script):
                self.script = script
                self.index = 0
                self.previous = None
                self.observations = []
                self.rng = np.random.default_rng(123)

            def read(self, count):
                if self.previous is not None:
                    iq, rate, center = manager.inference_segment()
                    self.observations.append((self.previous, len(iq), rate, center))
                if self.index == len(self.script):
                    return np.empty(0, np.complex64)
                label, frequency, offset, rate = self.script[self.index]
                self.center_frequency = frequency
                self.sample_rate = rate
                self.previous = label
                self.index += 1
                if offset is None:
                    return np.zeros(count, np.complex64)
                t = np.arange(count) / rate
                noise = 0.001 * (self.rng.standard_normal(count) + 1j * self.rng.standard_normal(count))
                return (0.5 * np.exp(2j * np.pi * offset * t) + noise).astype(np.complex64)

            def close(self):
                pass

        script = [("channel-a", 100e6, 12500, 100000)] * 70
        script += [("channel-b", 100e6, 30000, 100000),
                   ("whole-window", 100e6, None, 100000),
                   ("whole-window-continued", 100e6, None, 100000),
                   ("retuned-window", 101e6, None, 100000),
                   ("changed-rate", 101e6, None, 80000),
                   ("strong-rate-80", 101e6, 10000, 80000),
                   ("strong-rate-100", 101e6, 12500, 100000),
                   ("final-empty-window", 101e6, None, 100000)]
        receiver = ScriptedReceiver(script)
        with patch("signalclassifier.receivers._Synthetic", return_value=receiver):
            manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": 100000,
                           "block_size": 4096, "duration_seconds": 60, "spectrum_refresh_hz": 30})
            self.finish()
        self.assertIsNone(manager.status()["error"])
        a_observations = [value for value in receiver.observations if value[0] == "channel-a"]
        self.assertEqual(a_observations[0][1], 1024)  # one decimated input block
        self.assertGreater(a_observations[3][1], 2048)  # default trained model now gets a full window
        self.assertEqual(a_observations[-1][1], 65536)
        self.assertLessEqual(max(value[1] for value in receiver.observations), 65536)
        by_name = {value[0]: value for value in receiver.observations}
        self.assertEqual(by_name["channel-b"][1], 1024)
        self.assertEqual(by_name["whole-window"][1], 4096)
        self.assertEqual(by_name["whole-window-continued"][1], 8192)
        self.assertEqual(by_name["retuned-window"][1:], (4096, 100000, 101e6))
        self.assertEqual(by_name["changed-rate"][1:], (4096, 80000, 101e6))
        self.assertEqual(by_name["strong-rate-80"][1], 1366)
        self.assertEqual(by_name["strong-rate-100"][1], 1024)
        copied, _, _ = manager.inference_segment()
        copied[:] = 123
        np.testing.assert_array_equal(manager.inference_segment()[0], 0)
        # Restart must never append samples from the previous source/session.
        restarted = ScriptedReceiver([("new-session", 100e6, 12500, 100000)])
        with patch("signalclassifier.receivers._Synthetic", return_value=restarted):
            manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": 100000,
                           "block_size": 4096, "duration_seconds": 1, "spectrum_refresh_hz": 30})
            self.finish()
        self.assertEqual(len(manager.inference_segment()[0]), 1024)


if __name__ == "__main__":
    unittest.main()
