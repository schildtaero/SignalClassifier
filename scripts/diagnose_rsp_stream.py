#!/usr/bin/env python3
"""Compare RSP receive formats in the application's Python environment.

No application, FFT, classifier or recording is started. This bounded diagnostic
counts and continues past reported overflows; its samples are never saved or
used for training. It does not certify gap-free RF reception.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import time


def probe_format(soapy, numpy, name, config, *, clock=time.monotonic):
    """Use the same tune/gain/block request as the application's RSP adapter."""
    device = stream = None
    active = False
    result = {"format": name, "requested_sample_rate": config["sample_rate"],
              "center_frequency_hz": config["frequency"], "gain_db": config["gain"],
              "block_size": config["block_size"], "test_seconds": config["seconds"],
              "read_calls": 0, "samples": 0, "timeouts": 0, "empty_reads": 0, "overflows": 0,
              "read_ms_max": 0., "between_reads_ms_max": 0., "first_sample_after_seconds": None,
              "successful_read_ms_max": 0., "timeout_read_ms_max": 0., "overflow_read_ms_max": 0.,
              "first_overflow_after_seconds": None, "samples_before_first_overflow": None,
              "error_code": None, "error": None, "cleanup_errors": [],
              "records_iq": False, "gap_free_verified": False, "status": "completed"}
    started = None
    try:
        device = soapy.Device("driver=sdrplay")
        rx = soapy.SOAPY_SDR_RX
        device.setSampleRate(rx, 0, config["sample_rate"])
        device.setFrequency(rx, 0, config["frequency"])
        device.setGainMode(rx, 0, False)
        device.setGain(rx, 0, config["gain"])
        actual = float(device.getSampleRate(rx, 0))
        result["actual_sample_rate"] = actual if math.isfinite(actual) else None
        if not math.isfinite(actual) or abs(actual - config["sample_rate"]) > .001 * config["sample_rate"]:
            raise ValueError("Treiber hat eine andere als die angeforderte Abtastrate eingestellt")
        fmt = getattr(soapy, "SOAPY_SDR_" + name)
        stream = device.setupStream(rx, fmt, [0])
        if hasattr(device, "getStreamMTU"):
            result["stream_mtu_samples"] = int(device.getStreamMTU(stream))
        code = device.activateStream(stream)
        if code:
            raise RuntimeError(f"activateStream fehlgeschlagen: {code}")
        active = True
        started = previous_end = clock()
        while clock() - started < config["seconds"]:
            begin = clock()
            result["between_reads_ms_max"] = max(result["between_reads_ms_max"], (begin - previous_end) * 1000)
            # Fresh owned storage mirrors _RSP1A.read. CS16 uses two int16
            # scalars per complex sample; CF32 one complex64 element.
            count = config["block_size"]
            buffer = numpy.empty(2 * count, numpy.int16) if name == "CS16" else numpy.empty(count, numpy.complex64)
            response = device.readStream(stream, [buffer], count, timeoutUs=250_000)
            previous_end = clock()
            result["read_calls"] += 1
            result["read_ms_max"] = max(result["read_ms_max"], (previous_end - begin) * 1000)
            returned = int(response.ret)
            if returned > 0:
                result["successful_read_ms_max"] = max(result["successful_read_ms_max"], (previous_end - begin) * 1000)
                if returned > count:
                    raise RuntimeError("Treiber meldet mehr Samples als angefordert")
                result["samples"] += returned
                if result["first_sample_after_seconds"] is None:
                    result["first_sample_after_seconds"] = previous_end - started
            elif returned == getattr(soapy, "SOAPY_SDR_OVERFLOW", -4):
                result["overflows"] += 1
                result["overflow_read_ms_max"] = max(result["overflow_read_ms_max"], (previous_end - begin) * 1000)
                if result["first_overflow_after_seconds"] is None:
                    result["first_overflow_after_seconds"] = previous_end - started
                    result["samples_before_first_overflow"] = result["samples"]
            elif returned == getattr(soapy, "SOAPY_SDR_TIMEOUT", -1):
                result["timeouts"] += 1
                result["timeout_read_ms_max"] = max(result["timeout_read_ms_max"], (previous_end - begin) * 1000)
            elif returned == 0:
                result["empty_reads"] += 1
            else:
                result.update(status="error", error_code=returned, error="SoapySDR-Lesefehler")
                break
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        # Capture read timing before shutdown waits, matching the stream loop.
        if started is not None:
            result["elapsed_seconds"] = max(0., clock() - started)
        if stream is not None:
            if active:
                try:
                    device.deactivateStream(stream)
                except Exception as exc:
                    result["cleanup_errors"].append(f"deactivateStream: {type(exc).__name__}")
            try:
                device.closeStream(stream)
            except Exception as exc:
                result["cleanup_errors"].append(f"closeStream: {type(exc).__name__}")
        device = None
    elapsed = result.get("elapsed_seconds", 0)
    result["delivered_msps"] = result["samples"] / elapsed / 1e6 if elapsed > 0 else None
    if result["cleanup_errors"]:
        result["status"] = "error"
    elif result["status"] == "completed" and (result["overflows"] or result["timeouts"] or result["empty_reads"]):
        result["status"] = "completed_with_events"
    result["rate_scope"] = "Successfully returned samples per stream-loop wall time, including startup/timeouts/losses; not a verified RF clock rate."
    return result


def bounded_number(low, high):
    def parse(value):
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise argparse.ArgumentTypeError(f"Zahl zwischen {low:g} und {high:g} erforderlich")
        return number
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("both", "CS16", "CF32"), default="both")
    parser.add_argument("--seconds", type=bounded_number(1, 30), default=15.)
    parser.add_argument("--sample-rate", type=bounded_number(62500, 10e6), default=2e6)
    parser.add_argument("--frequency", type=bounded_number(1000, 2e9), default=100e6)
    parser.add_argument("--gain", type=bounded_number(-20, 100), default=30.)
    parser.add_argument("--block-size", type=int, choices=(4096, 16384, 65536, 131072), default=131072)
    args = parser.parse_args(argv)
    try:
        import numpy
        import SoapySDR
    except ImportError as exc:
        parser.exit(2, f"Import fehlgeschlagen: {exc}. Mit der Python-Umgebung der Anwendung ausführen.\n")
    print("SignalClassifier vorher beenden. RX-Diagnose ohne FFT, Modelle oder IQ-Dateien; Überläufe werden nur gezählt.", flush=True)
    print(json.dumps({"python": platform.python_version(), "numpy": numpy.__version__,
                      "soapy_library_version": SoapySDR.getLibVersion() if hasattr(SoapySDR, "getLibVersion") else None}), flush=True)
    results = []
    for name in (("CS16", "CF32") if args.format == "both" else (args.format,)):
        print(f"Teste {name} für {args.seconds:g} Sekunden …", flush=True)
        result = probe_format(SoapySDR, numpy, name, vars(args))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        results.append(result)
        if result["cleanup_errors"]:
            break
    return 1 if any(r["status"] == "error" or r["overflows"] or not r["samples"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
