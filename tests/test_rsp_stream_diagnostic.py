"""RSP format diagnostics use fake drivers, never physical RX or network."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_rsp_stream.py"
_SPEC = importlib.util.spec_from_file_location("rsp_stream_diagnostic_under_test", _PATH)
diagnostic = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(diagnostic)


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now


class Device:
    def __init__(self, clock, responses=(8,), *, actual_rate=None, activation=0,
                 read_error=None, deactivate_error=False, close_error=False, read_durations=()):
        self.clock, self.responses = clock, list(responses)
        self.actual_rate, self.activation = actual_rate, activation
        self.read_error = read_error
        self.read_durations = list(read_durations)
        self.deactivate_error, self.close_error = deactivate_error, close_error
        self.calls, self.reads = [], []
        self.stream = object()

    def setSampleRate(self, direction, channel, value):
        self.calls.append(("rate", direction, channel, value))
        self.requested_rate = value

    def setFrequency(self, direction, channel, value):
        self.calls.append(("frequency", direction, channel, value))

    def setGainMode(self, direction, channel, value):
        self.calls.append(("gain_mode", direction, channel, value))

    def setGain(self, direction, channel, value):
        self.calls.append(("gain", direction, channel, value))

    def getSampleRate(self, direction, channel):
        return self.requested_rate if self.actual_rate is None else self.actual_rate

    def setupStream(self, direction, fmt, channels):
        self.calls.append(("setup", direction, fmt, channels))
        return self.stream

    def getStreamMTU(self, stream):
        assert stream is self.stream
        return 65536

    def activateStream(self, stream):
        assert stream is self.stream
        self.calls.append(("activate",))
        if isinstance(self.activation, Exception):
            raise self.activation
        return self.activation

    def readStream(self, stream, buffers, count, *, timeoutUs):
        assert stream is self.stream
        assert len(buffers) == 1
        buffer = buffers[0]
        self.reads.append({"dtype": buffer.dtype, "size": buffer.size,
                           "nbytes": buffer.nbytes, "contiguous": buffer.flags.c_contiguous,
                           "count": count, "timeout_us": timeoutUs})
        self.clock.now += self.read_durations.pop(0) if self.read_durations else .1
        if self.read_error:
            raise self.read_error
        if not self.responses:
            raise AssertionError("Fake received an unexpected extra read")
        return SimpleNamespace(ret=self.responses.pop(0))

    def deactivateStream(self, stream):
        assert stream is self.stream
        self.calls.append(("deactivate",))
        if self.deactivate_error:
            raise RuntimeError("fake deactivate failure")

    def closeStream(self, stream):
        assert stream is self.stream
        self.calls.append(("close",))
        if self.close_error:
            raise RuntimeError("fake close failure")


class Soapy:
    SOAPY_SDR_RX = 1
    SOAPY_SDR_CS16 = "CS16"
    SOAPY_SDR_CF32 = "CF32"
    SOAPY_SDR_TIMEOUT = -1
    SOAPY_SDR_OVERFLOW = -4

    def __init__(self, device):
        self.device = device
        self.args = []

    def Device(self, args):
        self.args.append(args)
        return self.device

    @staticmethod
    def getLibVersion():
        return "fake-no-hardware"


class RspDiagnosticTests(unittest.TestCase):
    def config(self, **changes):
        return {"sample_rate": 2e6, "frequency": 100e6, "gain": 30.,
                "block_size": 8, "seconds": .05, **changes}

    def probe(self, *, name="CF32", config=None, **device_options):
        clock = Clock()
        device = Device(clock, **device_options)
        soapy = Soapy(device)
        result = diagnostic.probe_format(soapy, np, name, config or self.config(), clock=clock)
        return result, device, soapy

    def test_buffer_sizes_count_complex_samples_in_both_formats(self):
        for name, dtype, scalars, byte_count in (("CS16", np.int16, 16, 32),
                                                 ("CF32", np.complex64, 8, 64)):
            with self.subTest(format=name):
                result, device, soapy = self.probe(name=name)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(soapy.args, ["driver=sdrplay"])
                self.assertEqual(device.reads, [{"dtype": np.dtype(dtype), "size": scalars,
                    "nbytes": byte_count, "contiguous": True, "count": 8, "timeout_us": 250000}])
                self.assertIn(("setup", 1, name, [0]), device.calls)
                self.assertIn(("frequency", 1, 0, 100e6), device.calls)
                self.assertIn(("gain_mode", 1, 0, False), device.calls)
                self.assertIn(("gain", 1, 0, 30.), device.calls)
                self.assertEqual(device.calls[-2:], [("deactivate",), ("close",)])
                self.assertFalse(result["records_iq"])
                self.assertFalse(result["gap_free_verified"])

    def test_overflow_continues_and_throughput_uses_only_successful_samples(self):
        result, device, _ = self.probe(config=self.config(seconds=.35), responses=(8, -4, -1, 4))
        self.assertEqual(result["status"], "completed_with_events")
        self.assertEqual(result["read_calls"], 4)
        self.assertEqual(result["samples"], 12)
        self.assertEqual(result["overflows"], 1)
        self.assertEqual(result["timeouts"], 1)
        self.assertAlmostEqual(result["elapsed_seconds"], .4)
        self.assertAlmostEqual(result["first_sample_after_seconds"], .1)
        self.assertAlmostEqual(result["delivered_msps"], 12 / .4 / 1e6)
        self.assertAlmostEqual(result["read_ms_max"], 100.)
        self.assertEqual(len(device.reads), 4)
        self.assertFalse(result["gap_free_verified"])

    def test_unexpected_negative_code_is_reported_and_stream_closed(self):
        result, device, _ = self.probe(responses=(-3,))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], -3)
        self.assertEqual(result["samples"], 0)
        self.assertEqual(device.calls[-2:], [("deactivate",), ("close",)])

    def test_read_timing_kinds_empty_reads_and_first_overflow_are_distinct(self):
        result, _, _ = self.probe(config=self.config(seconds=.21),
            responses=(8, -4, -1, 0, 4, -4), read_durations=(.02, .03, .07, .01, .04, .05))
        self.assertEqual(result["read_calls"], 6)
        self.assertEqual(result["samples"], 12)
        self.assertEqual(result["overflows"], 2)
        self.assertEqual(result["timeouts"], 1)
        self.assertEqual(result["empty_reads"], 1)
        self.assertAlmostEqual(result["elapsed_seconds"], .22)
        self.assertAlmostEqual(result["successful_read_ms_max"], 40.)
        self.assertAlmostEqual(result["overflow_read_ms_max"], 50.)
        self.assertAlmostEqual(result["timeout_read_ms_max"], 70.)
        self.assertAlmostEqual(result["read_ms_max"], 70.)
        self.assertAlmostEqual(result["first_overflow_after_seconds"], .05)
        self.assertEqual(result["samples_before_first_overflow"], 8)
        self.assertFalse(result["gap_free_verified"])

    def test_exception_during_read_still_deactivates_and_closes(self):
        result, device, _ = self.probe(read_error=RuntimeError("fake read failure"))
        self.assertEqual(result["status"], "error")
        self.assertIn("fake read failure", result["error"])
        self.assertEqual(device.calls[-2:], [("deactivate",), ("close",)])

    def test_activation_failure_closes_unactivated_stream_without_reading(self):
        for activation in (-2, RuntimeError("fake activation failure")):
            with self.subTest(activation=activation):
                result, device, _ = self.probe(activation=activation)
                self.assertEqual(result["status"], "error")
                self.assertEqual(device.reads, [])
                self.assertEqual(device.calls[-1], ("close",))
                self.assertNotIn(("deactivate",), device.calls)
                self.assertIsNone(result["delivered_msps"])

    def test_ret_above_requested_count_is_not_counted_as_valid_samples(self):
        result, device, _ = self.probe(responses=(9,))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["samples"], 0)
        self.assertEqual(device.calls[-2:], [("deactivate",), ("close",)])

    def test_cleanup_failure_does_not_skip_close_or_claim_success(self):
        for options in ({"deactivate_error": True}, {"close_error": True}):
            with self.subTest(options=options):
                result, device, _ = self.probe(**options)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["cleanup_errors"])
                self.assertEqual(device.calls[-1], ("close",))

    def test_rate_mismatch_is_rejected_before_stream_setup(self):
        result, device, _ = self.probe(actual_rate=2.1e6)
        self.assertEqual(result["status"], "error")
        self.assertEqual(device.reads, [])
        self.assertFalse(any(call[0] == "setup" for call in device.calls))
        self.assertIsNone(result["delivered_msps"])

    def test_nonfinite_reported_rates_still_produce_valid_json(self):
        for rate in (float("nan"), float("inf")):
            with self.subTest(rate=rate):
                result, device, _ = self.probe(actual_rate=rate)
                self.assertEqual(result["status"], "error")
                self.assertEqual(device.reads, [])
                json.dumps(result, allow_nan=False)

    def test_cli_defaults_match_application_and_never_open_real_driver(self):
        fake = Soapy(None)
        successful = {"status": "completed", "cleanup_errors": [], "overflows": 0, "samples": 8}
        with patch.dict(sys.modules, {"SoapySDR": fake}), \
             patch.object(diagnostic, "probe_format", return_value=successful) as probe, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(diagnostic.main([]), 0)
        self.assertEqual([call.args[2] for call in probe.call_args_list], ["CS16", "CF32"])
        config = probe.call_args_list[0].args[3]
        self.assertEqual({key: config[key] for key in ("seconds", "sample_rate", "frequency", "gain", "block_size")},
                         {"seconds": 15., "sample_rate": 2e6, "frequency": 100e6, "gain": 30., "block_size": 131072})
        self.assertEqual(fake.args, [])

    def test_cli_rejects_unbounded_or_invalid_arguments_before_probe(self):
        cases = (("--seconds", "0"), ("--seconds", "31"), ("--seconds", "nan"),
                 ("--sample-rate", "0"), ("--sample-rate", "10000001"),
                 ("--frequency", "999"), ("--frequency", "2000000001"),
                 ("--gain", "-21"), ("--gain", "101"),
                 ("--block-size", "8192"), ("--format", "TX"))
        with patch.object(diagnostic, "probe_format") as probe:
            for flag, value in cases:
                with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        diagnostic.main([flag, value])
                    self.assertEqual(error.exception.code, 2)
            probe.assert_not_called()

    def test_cli_reports_overflow_as_failure_and_stops_after_cleanup_error(self):
        fake = Soapy(None)
        for result, expected_calls in (({"status": "completed_with_events", "cleanup_errors": [], "overflows": 1, "samples": 8}, 2),
                                       ({"status": "error", "cleanup_errors": ["closeStream"], "overflows": 0, "samples": 8}, 1)):
            with self.subTest(result=result), patch.dict(sys.modules, {"SoapySDR": fake}), \
                 patch.object(diagnostic, "probe_format", return_value=result) as probe, redirect_stdout(io.StringIO()):
                self.assertEqual(diagnostic.main([]), 1)
                self.assertEqual(probe.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main()
