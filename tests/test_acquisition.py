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
        observed = self.manager.analysis_snapshots()[0]
        self.assertEqual(observed["quality"]["reported_gaps"], 1)
        self.assertEqual(observed["quality"]["overflows"], 1)
        self.assertEqual(observed["quality"]["capture_error"], "SDR overflow")
        self.assertEqual(observed["quality"]["capture_state"], "error")
        self.assertTrue(observed["quality"]["contiguous"])  # saved logical prefix, RF continuity remains unverified

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

    def test_synthetic_replay_provenance_survives_derived_recording_and_second_replay(self):
        iq = np.exp(2j * np.pi * .125 * np.arange(4096)).astype(np.complex64)
        path = write_recording(Path(self.directory.name) / "generated", iq, 100000, 145e6,
                               extra={"sc:source": "synthetic", "signalclassifier:group_id": "generated-origin"})
        started = self.manager.start({"source": "recording", "recording_path": str(path),
                                      "mode": "wideband", "block_size": 4096, "duration_seconds": 1})
        self.assertTrue(started["synthetic"])
        self.finish()
        for public in (self.manager.status(), self.manager.snapshot(), self.manager.analysis_snapshots()[0]):
            self.assertTrue(public["synthetic"])
            self.assertTrue(public["declared_source_synthetic"])
            self.assertEqual(public["data_provenance"]["origin_kind"], "synthetic")
            self.assertFalse(public["data_provenance"]["rf_origin_verified"])
        self.assertEqual(self.manager.analysis_snapshots()[0]["origin_group_id"], "generated-origin")
        derived = self.manager.list_recordings()[0]
        self.assertTrue(derived["synthetic"])
        self.assertEqual(derived["group_id"], "generated-origin")
        self.assertEqual(derived["metadata"]["global"]["sc:source"], "recording")
        self.assertTrue(derived["metadata"]["global"]["sc:declared_source_synthetic"])
        self.manager.start({"source": "recording", "recording_path": derived["metadata_path"],
                            "mode": "monitor", "block_size": 4096, "duration_seconds": 1})
        self.finish()
        observed = self.manager.analysis_snapshots()[0]
        self.assertTrue(observed["synthetic"])
        self.assertEqual(observed["origin_group_id"], "generated-origin")

    def test_nested_synthetic_metadata_is_declared_and_absence_does_not_verify_real_rf(self):
        iq = np.exp(2j * np.pi * .125 * np.arange(4096)).astype(np.complex64)
        for name, extra, expected in (
            ("nested", {"sc:source": "recording", "sc:data_provenance": {
                "parent_provenance": {"metadata": {"global": {"sc:source": "synthetic"}}}}}, True),
            ("unknown", {}, False),
        ):
            with self.subTest(name=name):
                path = write_recording(Path(self.directory.name) / name, iq, 100000, 145e6, extra=extra)
                self.manager.start({"source": "recording", "recording_path": str(path),
                                    "mode": "monitor", "block_size": 4096, "duration_seconds": 1})
                self.finish()
                observed = self.manager.analysis_snapshots()[0]
                self.assertEqual(observed["synthetic"], expected)
                provenance = observed["data_provenance"]
                self.assertEqual(provenance["origin_kind"], "synthetic" if expected else "unverified_recording")
                self.assertFalse(provenance["rf_origin_verified"])
                self.assertEqual(bool(provenance["metadata_evidence_paths"]), expected)

    def test_scan_tiles_share_capture_group_but_keep_distinct_sessions(self):
        sessions = []
        iq = np.exp(2j * np.pi * .125 * np.arange(4096)).astype(np.complex64)
        for center in (433e6, 434e6):
            source = self.ArraySource(iq, 100000)
            source.center_frequency = center
            with patch("signalclassifier.receivers._Synthetic", return_value=source):
                self.manager.start({"source": "synthetic", "mode": "wideband", "sample_rate": 100000,
                                    "block_size": 4096, "duration_seconds": 1,
                                    "capture_group_id": "scan-shared-origin", "scan_id": "scan-123"})
                self.finish()
            sessions.append(self.manager.status()["session_id"])
            self.assertEqual(self.manager.analysis_snapshots()[0]["origin_group_id"], "scan-shared-origin")
        self.assertNotEqual(sessions[0], sessions[1])
        recordings = self.manager.list_recordings()
        self.assertEqual(len(recordings), 2)
        for recording in recordings:
            metadata = recording["metadata"]["global"]
            self.assertEqual(recording["group_id"], "scan-shared-origin")
            self.assertEqual(metadata["signalclassifier:group_id"], "scan-shared-origin")
            self.assertEqual(metadata["sc:origin_group_id"], "scan-shared-origin")
            self.assertEqual(metadata["sc:scan_id"], "scan-123")
            self.assertIn(metadata["sc:session_id"], sessions)

    def test_capture_group_cannot_override_replay_origin_and_ids_are_bounded(self):
        for key in ("capture_group_id", "scan_id"):
            for value in ("", "x" * 129, "a\nb", "a\u0085b", "a\u202eb", None, 1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_config({"source": "synthetic", key: value})
        self.assertEqual(validate_config({"source": "synthetic", "capture_group_id": "Ü" * 128})["capture_group_id"], "Ü" * 128)
        iq = np.exp(2j * np.pi * .125 * np.arange(4096)).astype(np.complex64)
        path = write_recording(Path(self.directory.name) / "replay-origin", iq, 100000, 433e6,
                               extra={"signalclassifier:group_id": "immutable-original-origin"})
        self.manager.start({"source": "recording", "recording_path": str(path), "mode": "wideband",
                            "block_size": 4096, "duration_seconds": 1, "capture_group_id": "cannot-replace-origin"})
        self.finish()
        self.assertEqual(self.manager.analysis_snapshots()[0]["origin_group_id"], "immutable-original-origin")
        recording = self.manager.list_recordings()[0]
        self.assertEqual(recording["group_id"], "immutable-original-origin")
        self.assertEqual(recording["metadata"]["global"]["sc:origin_group_id"], "immutable-original-origin")

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

    def test_multi_signal_rings_keep_ids_across_strength_changes_and_copy_atomically(self):
        manager = self.manager

        class TwoSignals:
            sample_rate = 100000.0
            center_frequency = 100e6
            paced = False
            index = 0

            def __init__(self):
                self.observations = []

            def read(self, count):
                snapshots = manager.analysis_snapshots()
                if snapshots:
                    self.observations.append({round(x["center_frequency_hz"]):
                                              (x["channel_id"], x["sample_count"], x["source_epoch"])
                                              for x in snapshots})
                if self.index == 72:
                    return np.empty(0, np.complex64)
                t = (np.arange(count) + self.index * count) / self.sample_rate
                amplitudes = (.7, .25) if self.index < 36 else (.25, .7)
                self.index += 1
                return (amplitudes[0] * np.exp(-2j * np.pi * 25000 * t)
                        + amplitudes[1] * np.exp(2j * np.pi * 25000 * t)).astype(np.complex64)

            def close(self):
                pass

        source = TwoSignals()
        with patch("signalclassifier.receivers._Synthetic", return_value=source):
            manager.start({"source": "synthetic", "sample_rate": 100000, "mode": "monitor",
                           "block_size": 4096, "spectrum_refresh_hz": 30, "duration_seconds": 60})
            self.finish()
        initial, final = source.observations[0], source.observations[-1]
        self.assertEqual(len(initial), 2)
        for frequency in initial:
            self.assertEqual(initial[frequency][0], final[frequency][0])
            self.assertEqual(initial[frequency][2], final[frequency][2])
            self.assertEqual(initial[frequency][1], 1024)
            self.assertEqual(final[frequency][1], 65536)
        snapshots = manager.analysis_snapshots()
        self.assertEqual(len(snapshots), 2)
        for entry in snapshots:
            self.assertEqual(entry["iq"].dtype, np.complex64)
            self.assertAlmostEqual(entry["duration_seconds"], 65536 / entry["sample_rate"])
            self.assertEqual(entry["source_sample_stop"] - entry["source_sample_start"],
                             (entry["sample_count"] - 1) * entry["source_sample_stride"] + 1)
            self.assertLessEqual(entry["source_sample_stop"], entry["source_block_stop"])
            self.assertTrue(entry["quality"]["contiguous"])
            self.assertFalse(entry["quality"]["gap_free_verified"])
            self.assertFalse(entry["rf_timestamp_verified"])
            self.assertGreater(abs(np.mean(entry["iq"][-2048:])), .2)
        # Repeated reads of a finished observation preserve identity/time; callers
        # cannot mutate either IQ or nested detection/quality state in the manager.
        original_id, original_time = snapshots[0]["observation_id"], snapshots[0]["observed_at"]
        snapshots[0]["iq"][:] = 999
        snapshots[0]["detection"]["method"] = "changed"
        snapshots[0]["quality"]["contiguous"] = False
        fresh = manager.analysis_snapshots(max_channels=1, max_samples=2048)[0]
        self.assertEqual(fresh["observation_id"], original_id)
        self.assertEqual(fresh["observed_at"], original_time)
        self.assertEqual(len(fresh["iq"]), 2048)
        self.assertLess(np.max(np.abs(fresh["iq"])), 2)
        self.assertEqual(fresh["detection"]["method"], "median_bin_energy")
        self.assertTrue(fresh["quality"]["contiguous"])
        manager.stop()
        self.assertEqual(manager.analysis_snapshots()[0]["observation_id"], original_id)

    def test_multi_signal_limit_is_explicit_and_all_sixteen_channels_are_supported(self):
        rate = 1000000
        t = np.arange(16384) / rate
        iq = sum(.03 * np.exp(2j * np.pi * f * t) for f in np.linspace(-375000, 375000, 16)).astype(np.complex64)
        for limit in (8, 16):
            with self.subTest(limit=limit):
                source = self.ArraySource(iq, rate)
                with patch("signalclassifier.receivers._Synthetic", return_value=source):
                    config = {"source": "synthetic", "sample_rate": rate, "mode": "monitor",
                              "block_size": len(iq), "duration_seconds": 1}
                    if limit != 8:
                        config["analysis_max_channels"] = limit
                    self.manager.start(config)
                    self.finish()
                status = self.manager.snapshot()["analysis_status"]
                self.assertEqual(status["detected_channels"], 16)
                self.assertEqual(status["buffered_channels"], limit)
                self.assertEqual(status["unbuffered_detections_count"], 16 - limit)
                self.assertEqual(status["channel_limit"], limit)
                self.assertEqual(status["scope"], "current_receiver_window")
                self.assertLessEqual(status["buffer_bytes"], limit * 65536 * 8)
                self.assertEqual(len(self.manager.analysis_snapshots()), limit)
        for value in (0, 17, True, 2.5):
            with self.assertRaises(ValueError):
                validate_config({"source": "synthetic", "analysis_max_channels": value})
        with self.assertRaises(ValueError):
            self.manager.analysis_snapshots(max_channels=17)
        with self.assertRaises(ValueError):
            self.manager.analysis_snapshots(max_samples=65537)

    def test_energy_analysis_reuses_recorded_output_and_preserves_replay_origin(self):
        rate = 200000
        t = np.arange(4 * 4096) / rate
        iq = (.5 * np.exp(-2j * np.pi * 50000 * t) + .8 + .25 * np.exp(2j * np.pi * 50000 * t)).astype(np.complex64)
        path = write_recording(Path(self.directory.name) / "three-signals", iq, rate, 433e6,
                               extra={"signalclassifier:group_id": "original-origin"})
        calls = []
        original_process = _Channelizer.process

        def count_process(extractor, block):
            calls.append(id(extractor))
            return original_process(extractor, block)

        with patch.object(_Channelizer, "process", count_process):
            self.manager.start({"source": "recording", "recording_path": str(path), "mode": "energy",
                                "max_channels": 1, "analysis_max_channels": 3,
                                "block_size": 4096, "duration_seconds": 1})
            self.finish()
        self.assertEqual(len(self.manager.list_recordings()), 1)
        snapshots = self.manager.analysis_snapshots()
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(x["origin_group_id"] == "original-origin" for x in snapshots))
        self.assertEqual(len(calls), 3 * 4)  # one recording filter + two analysis-only filters; no legacy duplicate
        self.assertEqual(len(set(calls)), 3)
        self.assertEqual(self.manager.snapshot()["analysis_status"]["extra_channelizers"], 2)
        self.assertEqual(self.manager.list_recordings()[0]["group_id"], "original-origin")

    def test_analysis_contexts_reset_and_ended_burst_metadata_is_bounded(self):
        manager = self.manager

        class ContextSource:
            center_frequency = 100e6
            sample_rate = 100000.0
            paced = False

            def __init__(self, script):
                self.script = iter(script)
                self.previous = None
                self.observations = {}

            def read(self, count):
                if self.previous is not None:
                    self.observations[self.previous] = manager.analysis_snapshots()
                item = next(self.script, None)
                if item is None:
                    return np.empty(0, np.complex64)
                self.previous, self.center_frequency, self.sample_rate, active = item
                if not active:
                    return np.zeros(count, np.complex64)
                return np.exp(2j * np.pi * .25 * np.arange(count)).astype(np.complex64)

            def close(self):
                pass

        source = ContextSource([("a", 100e6, 100000, True), ("none", 100e6, 100000, False),
                                ("again", 100e6, 100000, True), ("retuned", 101e6, 100000, True),
                                ("rate", 101e6, 80000, True)])
        with patch("signalclassifier.receivers._Synthetic", return_value=source):
            manager.start({"source": "synthetic", "sample_rate": 100000, "mode": "monitor",
                           "block_size": 4096, "spectrum_refresh_hz": 30, "duration_seconds": 60})
            self.finish()
        obs = source.observations
        self.assertEqual(obs["none"], [])
        self.assertNotEqual(obs["a"][0]["channel_id"], obs["again"][0]["channel_id"])
        self.assertEqual(obs["a"][0]["source_epoch"], obs["again"][0]["source_epoch"])
        self.assertNotEqual(obs["again"][0]["source_epoch"], obs["retuned"][0]["source_epoch"])
        self.assertNotEqual(obs["retuned"][0]["source_epoch"], obs["rate"][0]["source_epoch"])
        self.assertTrue(all(len(obs[name][0]["iq"]) <= 1366 for name in ("a", "again", "retuned", "rate")))
        ended = manager.snapshot()["analysis_status"]["ended_channels"]
        self.assertEqual(len(ended), 3)
        self.assertTrue(all(not x["iq_retained"] and "iq" not in x for x in ended))
        old_epoch = obs["rate"][0]["source_epoch"]
        # More ended bursts than the metadata capacity cannot grow memory without
        # bound; evictions and missing IQ are explicitly exposed to callers.
        script = [(str(i), 100e6, 100000, i % 2 == 0) for i in range(80)]
        source = ContextSource(script)
        with patch("signalclassifier.receivers._Synthetic", return_value=source):
            manager.start({"source": "synthetic", "sample_rate": 100000, "mode": "monitor",
                           "block_size": 4096, "spectrum_refresh_hz": 30, "duration_seconds": 60})
            self.finish()
        status = manager.snapshot()["analysis_status"]
        self.assertEqual(status["buffered_channels"], 0)
        self.assertEqual(len(status["ended_channels"]), 32)
        self.assertEqual(status["ended_channels_evicted"], 8)
        self.assertNotEqual(status["ended_channels"][-1]["source_epoch"], old_epoch)
        json.dumps(manager.snapshot())  # public status remains metadata-only JSON

    def test_unresolved_window_probe_reuses_bounded_legacy_ring_and_returns_metadata_copies(self):
        self.assertIsNone(self.manager.analysis_probe_snapshot())
        count = 20 * 4096 + 17
        with patch("signalclassifier.receivers._Synthetic", return_value=self.ArraySource(np.zeros(count, np.complex64), 100000)):
            self.manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": 100000,
                                "block_size": 4096, "duration_seconds": 60})
            self.finish()
        self.assertEqual(self.manager.analysis_snapshots(), [])
        probe = self.manager.analysis_probe_snapshot()
        self.assertEqual(probe["observation_type"], "unresolved_window_probe")
        self.assertEqual(probe["detection"]["method"], "whole_window_probe")
        self.assertFalse(probe["detection"]["detected"])
        self.assertEqual(probe["sample_count"], 65536)
        self.assertEqual(probe["source_sample_stride"], 1)
        self.assertEqual(probe["source_sample_stop"], count)
        self.assertEqual(probe["source_sample_start"], count - 65536)
        self.assertEqual(probe["source_block_stop"], count)
        self.assertEqual(probe["bandwidth_hz"], 100000)
        self.assertAlmostEqual(probe["duration_seconds"], .65536)
        self.assertTrue(probe["synthetic"])
        self.assertFalse(probe["rf_timestamp_verified"])
        self.assertTrue(probe["quality"]["contiguous"])
        self.assertFalse(probe["quality"]["gap_free_verified"])
        self.assertEqual(probe["data_provenance"]["origin_kind"], "synthetic")
        self.assertTrue(self.manager.snapshot()["analysis_status"]["unresolved_window_probe_available"])
        # The producer retains exactly its existing immutable legacy IQ array.
        with self.manager._lock:
            self.assertIs(self.manager._analysis_probe["iq"], self.manager.latest_iq)
        probe["iq"][:] = 99
        probe["detection"]["detected"] = True
        small = self.manager.analysis_probe_snapshot(max_samples=1024)
        np.testing.assert_array_equal(small["iq"], 0)
        self.assertFalse(small["detection"]["detected"])
        self.assertEqual(small["source_sample_start"], count - 1024)
        self.assertEqual(small["observed_at"], probe["observed_at"])
        self.assertEqual(small["observation_id"], probe["observation_id"])
        with self.assertRaises(ValueError):
            self.manager.analysis_probe_snapshot(max_samples=65537)
        # An independently replaced legacy context cannot yield a stale probe.
        with self.manager._lock:
            self.manager.latest_sample_rate = 80000
        self.assertIsNone(self.manager.analysis_probe_snapshot())

    def test_unresolved_window_probe_gates_strongest_and_resets_retune_rate_restart(self):
        manager = self.manager

        class ProbeSource:
            sample_rate = 100000.0
            center_frequency = 100e6
            paced = False

            def __init__(self, script):
                self.script = iter(script)
                self.previous = None
                self.observations = {}
                self.rng = np.random.default_rng(919)

            def read(self, count):
                if self.previous:
                    self.observations[self.previous] = manager.analysis_probe_snapshot()
                item = next(self.script, None)
                if item is None:
                    return np.empty(0, np.complex64)
                self.previous, self.center_frequency, self.sample_rate, tone = item
                block = .01 * (self.rng.standard_normal(count) + 1j * self.rng.standard_normal(count))
                if tone:
                    block += .7 * np.exp(2j * np.pi * .125 * np.arange(count))
                return block.astype(np.complex64)

            def close(self):
                pass

        source = ProbeSource([("noise", 100e6, 100000, False), ("strong", 100e6, 100000, True),
                              ("again", 100e6, 100000, False), ("retuned", 101e6, 100000, False),
                              ("rate", 101e6, 80000, False)])
        with patch("signalclassifier.receivers._Synthetic", return_value=source):
            manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": 100000,
                           "block_size": 4096, "spectrum_refresh_hz": 30, "threshold_db": 20, "duration_seconds": 60})
            self.finish()
        obs = source.observations
        self.assertIsNone(obs["strong"])
        for name in ("noise", "again", "retuned", "rate"):
            self.assertIsNotNone(obs[name])
            self.assertEqual(obs[name]["sample_count"], 4096)
            self.assertFalse(obs[name]["detection"]["detected"])
        self.assertEqual(obs["again"]["source_sample_start"], 8192)
        self.assertEqual(obs["again"]["source_sample_stop"], 12288)
        self.assertEqual(obs["noise"]["source_epoch"], obs["again"]["source_epoch"])
        self.assertNotEqual(obs["again"]["source_epoch"], obs["retuned"]["source_epoch"])
        self.assertNotEqual(obs["retuned"]["source_epoch"], obs["rate"]["source_epoch"])
        self.assertEqual(obs["retuned"]["center_frequency_hz"], 101e6)
        self.assertEqual(obs["rate"]["sample_rate"], 80000)
        self.assertEqual(obs["rate"]["bandwidth_hz"], 80000)
        previous_epoch = obs["rate"]["source_epoch"]
        restarted = ProbeSource([("new", 100e6, 100000, False)])
        with patch("signalclassifier.receivers._Synthetic", return_value=restarted):
            manager.start({"source": "synthetic", "mode": "monitor", "sample_rate": 100000,
                           "block_size": 4096, "threshold_db": 20, "duration_seconds": 60})
            self.finish()
        self.assertNotEqual(manager.analysis_probe_snapshot()["source_epoch"], previous_epoch)


if __name__ == "__main__":
    unittest.main()
