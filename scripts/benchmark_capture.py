#!/usr/bin/env python3
"""Reproducible numerical capture workload; no receiver hardware is accessed."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import scipy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signalclassifier.receivers import CaptureManager


def run(rate=5_000_000, block_size=131072, blocks=24, channels=8, mode="energy", hardware_queue=False):
    """Same deterministic input and processing settings for each mode/revision."""
    rng = np.random.default_rng(892)
    t = np.arange(block_size, dtype=np.float64) / rate
    block = (.001 * (rng.standard_normal(block_size) + 1j * rng.standard_normal(block_size))).astype(np.complex64)
    bins = (-700, -500, -300, -100, 100, 300, 500, 700)[:channels]
    for index in bins:
        block += (.08 * np.exp(2j * np.pi * (rate * index / 2048) * t)).astype(np.complex64)

    class Source:
        sample_rate = rate
        center_frequency = 433e6
        paced = False
        owns_read_buffers = True

        def __init__(self):
            self.left = blocks

        def read(self, count):
            self.left -= 1
            return block.copy() if self.left >= 0 else np.empty(0, np.complex64)

        def close(self):
            pass

    # Default excludes hardware/producer pacing to measure the numerical pipeline.
    # Optional queue exercise is intentionally an unpaced burst, not RF timing.
    source_name = "rsp1a" if hardware_queue else "synthetic"
    adapter = "_RSP1A" if hardware_queue else "_Synthetic"
    with tempfile.TemporaryDirectory(prefix="signalclassifier-benchmark-") as directory:
        manager = CaptureManager(directory)
        with patch(f"signalclassifier.receivers.{adapter}", return_value=Source()):
            started = time.perf_counter()
            manager.start({"source": source_name, "mode": mode, "sample_rate": rate,
                           "center_frequency_hz": 433e6, "block_size": block_size,
                           "duration_seconds": max(60, blocks * block_size / rate * 20),
                           "max_bytes": max(1024 ** 3, blocks * block_size * 8),
                           "max_channels": channels})
            manager._thread.join(timeout=300)
            elapsed = time.perf_counter() - started
            if manager._thread.is_alive():
                manager.stop()
                raise RuntimeError("Numerischer Benchmark hat 300 Sekunden überschritten.")
        status = manager.status()
        rf_seconds = status["samples"] / rate
        return {"workload": "deterministic tones plus noise; no hardware",
                "mode": mode, "sample_rate": rate, "block_size": block_size,
                "input_blocks": blocks, "tone_count": channels, "elapsed_seconds": elapsed,
                "rf_seconds": rf_seconds, "wall_rtf": elapsed / rf_seconds if rf_seconds else None,
                "saved_recordings": len(manager.list_recordings()), "bytes_written": status["bytes_written"],
                "performance": status["performance"], "error": status["error"],
                "hardware_queue_exercised": hardware_queue,
                "environment": {"platform": platform.platform(), "processor": platform.processor(),
                                "python": platform.python_version(), "numpy": np.__version__,
                                "scipy": scipy.__version__}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, default=5_000_000)
    parser.add_argument("--block-size", type=int, default=131072)
    parser.add_argument("--blocks", type=int, default=24)
    parser.add_argument("--channels", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--mode", choices=["monitor", "wideband", "energy", "all"], default="all")
    parser.add_argument("--hardware-queue", action="store_true", help="Exercise the hardware queue with an unpaced numerical burst")
    args = parser.parse_args()
    if not 1 <= args.blocks <= 1024:
        parser.error("--blocks muss zwischen 1 und 1024 liegen")
    modes = ["monitor", "wideband", "energy"] if args.mode == "all" else [args.mode]
    for mode in modes:
        print(json.dumps(run(args.rate, args.block_size, args.blocks, args.channels, mode, args.hardware_queue),
                         ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
