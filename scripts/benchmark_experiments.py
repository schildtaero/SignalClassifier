#!/usr/bin/env python3
"""Profile small, sequential experiments without an SDR or persistent models.

Examples:
  .venv/bin/python scripts/benchmark_experiments.py --output /tmp/sdr-profile.json
  .venv/bin/python scripts/benchmark_experiments.py --source-root /tmp/frozen-source
  .venv/bin/python scripts/benchmark_experiments.py --dataset-workers --output /tmp/dataset-workers.json

CPU profiles explain host work; they do not measure CUDA kernel utilisation.
The profiler adds overhead, so use these measurements for phase diagnosis rather
than production p95 ranking. Temporary model files are removed on completion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import cProfile
from datetime import datetime, timezone
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import pstats
import sys
import tempfile
import time


def benchmark_dataset_workers(ml, args):
    """Measure the implemented deterministic preparation plan, without a cache."""
    import numpy as np
    from scipy import __version__ as scipy_version
    if not hasattr(ml, "_EXECUTION_CONTEXT"):
        raise ValueError("This source does not expose the integrated preprocessing worker context")
    if not 1 <= args.dataset_repetitions <= 5 or any(not 32 <= size <= 1024 for size in args.dataset_sizes):
        raise ValueError("Dataset diagnostic budget: 1..5 repetitions, 32..1024 segments")
    package = Path(ml.__file__).resolve().parent
    output = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "profile_kind": "dataset_worker_microbenchmark", "source_root": str(args.source_root.resolve()),
              "source_files_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.glob("*.py"))},
              "hardware": ml.hardware_profile(), "numpy": np.__version__, "scipy": scipy_version,
              "parallel_threshold": getattr(ml, "PREPROCESSING_PARALLEL_THRESHOLD", None),
              "config": {"segment_length": 2048, "sample_rate": 256000, "seed": 42,
                         "snr_db": [-5, 5, 15], "jitter": [0, .1],
                         "augmentation": {"frequency_offset_hz": 1200, "sample_clock_ppm": 10,
                                          "iq_gain_db": 1, "iq_phase_deg": 3, "dc_offset": .01,
                                          "echo_amplitude": .1, "echo_delay_samples": 8}},
              "environment": {key: os.environ.get(key) for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")},
              "warmup_per_cell": 1, "repetitions": args.dataset_repetitions, "rows": [],
              "limitations": ["One CPU host; no training started by this harness, external host load not controlled. This does not predict every workstation or GPU workload.",
                              "Times include executor construction, deterministic job planning, augmentation and final stacking; synthetic source generation is outside the measured interval.",
                              "No feature cache is built. All worker settings use the same prebuilt IQ, RNG seed and augmentation configuration.",
                              "Worker order rotates between repetitions. Median values are diagnostic, not isolated production inference ranking measurements."]}
    config = output["config"]
    entries = [{"iq": ml.generate_signal("AM" if index % 2 == 0 else "FM", 2048, seed=1000 + index),
                "label": index % 2, "sample_rate": 256000, "group_id": f"source-{index}"}
               for index in range(max(args.dataset_sizes))]
    def prepare(selected, representation, workers):
        token = ml._EXECUTION_CONTEXT.set({"resolved_cpu_threads": 1, "resolved_preprocessing_workers": workers})
        stats = {}
        stats_token = ml._DATASET_EXECUTION.set(stats)
        try:
            started = time.perf_counter()
            values = ml._dataset(selected, {"input": representation}, config, np.random.default_rng(42))
            return values, time.perf_counter() - started, stats.get("max_workers_used", 1)
        finally:
            ml._DATASET_EXECUTION.reset(stats_token)
            ml._EXECUTION_CONTEXT.reset(token)
    for size in args.dataset_sizes:
        selected = entries[:size]
        for representation in ("features", "iq", "spectrogram"):
            reference, _, _ = prepare(selected, representation, 1)
            for workers in (2, 4):
                prepare(selected, representation, workers)
            measurements = {workers: [] for workers in (1, 2, 4)}
            actual = {}
            for repeat in range(args.dataset_repetitions):
                order = (1, 2, 4)
                order = order[repeat % 3:] + order[:repeat % 3]
                for workers in order:
                    values, elapsed, used = prepare(selected, representation, workers)
                    if not all(np.array_equal(value, baseline) for value, baseline in zip(values, reference)):
                        raise ValueError(f"Nonidentical dataset output: {representation}, {size} segments, {workers} workers")
                    measurements[workers].append(elapsed)
                    actual[workers] = used
            serial = float(np.median(measurements[1]))
            for workers in (1, 2, 4):
                median = float(np.median(measurements[workers]))
                row = {"representation": representation, "segments": size, "segment_length": 2048,
                       "workers_requested": workers, "workers_used": actual[workers],
                       "median_seconds": median, "seconds": measurements[workers],
                       "speedup_vs_serial": serial / median, "outputs_bit_identical": True}
                output["rows"].append(row)
                print(json.dumps(row), flush=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topologies", nargs="+", default=["feature-lda", "feature-mlp-32", "iq-cnn-16"])
    parser.add_argument("--samples-per-class", type=int, default=48)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--segment-length", type=int, default=2048)
    parser.add_argument("--dataset-workers", action="store_true", help="Instead compare _dataset with 1/2/4 preprocessing threads")
    parser.add_argument("--dataset-sizes", type=int, nargs="+", default=[128, 512])
    parser.add_argument("--dataset-repetitions", type=int, default=3)
    args = parser.parse_args()
    if not 12 <= args.samples_per_class <= 128 or not 1 <= args.epochs <= 10 or not 256 <= args.segment_length <= 8192:
        parser.error("Diagnostic budget: samples 12..128, epochs 1..10, segment length 256..8192")
    sys.path.insert(0, str(args.source_root.resolve()))
    from signalclassifier import ml
    if args.dataset_workers:
        return benchmark_dataset_workers(ml, args)

    # Separate initial imports/optimizer lazy imports from measured steady work.
    warmup_start = time.perf_counter()
    torch_available = False
    cuda_available = False
    try:
        import torch
        torch_available, cuda_available = True, torch.cuda.is_available()
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        warmup_model = torch.nn.Linear(2, 2)
        warmup_optimizer = torch.optim.Adam(warmup_model.parameters())
        warmup_model(torch.ones(2, 2)).sum().backward()
        warmup_optimizer.step()
        del warmup_model, warmup_optimizer
    except ImportError:
        pass
    warmup_seconds = time.perf_counter() - warmup_start
    package = Path(ml.__file__).resolve().parent
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.glob("*.py"))}
    original_functions = {name: getattr(ml, name) for name in ("_dataset", "classify", "generate_signal")}
    output = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "profile_kind": "instrumented_cpu_diagnostic", "cuda_available": cuda_available,
              "source_root": str(args.source_root.resolve()), "source_files_sha256": source_hashes,
              "hardware": ml.hardware_profile(), "warmup_seconds": warmup_seconds,
              "environment": {key: os.environ.get(key) for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")},
              "limitations": ["CPU only; no CUDA utilisation or GPU speed-up measured.",
                              "cProfile and Python phase wrappers add overhead; these latency values must not populate production model rankings.",
                              "One small run per topology, fixed synthetic dataset, warmed Python/Torch optimizer imports; no repeated statistical benchmark.",
                              "Host contention is not controlled; phase wall times overlap with their cumulative function totals and must not be added twice."],
              "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for topology in args.topologies:
        if topology != "feature-lda" and not torch_available:
            output["runs"].append({"topology_id": topology, "status": "skipped", "reason": "PyTorch unavailable"})
            continue
        phases = defaultdict(lambda: {"calls": 0, "wall_seconds": 0.})
        def instrument(name):
            original = original_functions[name]
            @functools.wraps(original)
            def measured(*values, **kwargs):
                phase = name
                if name == "_dataset":
                    caller = inspect.currentframe().f_back
                    groups = caller.f_locals.get("groups", {})
                    split = next((key for key, entries in groups.items() if entries is values[0]), "unknown")
                    snr = kwargs.get("snr", values[4] if len(values) > 4 else None)
                    phase += ":" + split + (":grid" if isinstance(snr, (int, float)) else ":" + str(snr or "random"))
                    del caller
                started = time.perf_counter()
                try:
                    return original(*values, **kwargs)
                finally:
                    phases[phase]["calls"] += 1
                    phases[phase]["wall_seconds"] += time.perf_counter() - started
            return measured
        for name in original_functions:
            setattr(ml, name, instrument(name))
        config = {"source": "synthetic", "topology_id": topology, "seed": 42,
                  "samples_per_class": args.samples_per_class, "segment_length": args.segment_length,
                  "epochs": args.epochs, "batch_size": 32, "learning_rate": .001,
                  "snr_db": [-5, 5, 15], "jitter": [0, .1], "device": "cpu"}
        profiler = cProfile.Profile()
        started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="signalclassifier-profile-") as directory:
                report = profiler.runcall(ml.run_experiment, config, Path(directory))
            elapsed = time.perf_counter() - started
            statistics = pstats.Stats(profiler)
            functions = []
            for (filename, line, name), (primitive, calls, self_time, cumulative, _) in statistics.stats.items():
                functions.append({"file": filename, "line": line, "function": name, "calls": calls,
                                  "primitive_calls": primitive, "self_seconds": self_time, "cumulative_seconds": cumulative})
            functions.sort(key=lambda value: value["cumulative_seconds"], reverse=True)
            metrics = report["metrics"]
            row = {"topology_id": topology, "status": "completed", "config": config,
                   "elapsed_seconds": elapsed, "reported_training_seconds": metrics["training_seconds"],
                   "reported_preprocessing_seconds": metrics["preprocessing_seconds"], "phases": dict(phases),
                   "other_wall_seconds": elapsed - metrics["training_seconds"] - metrics["preprocessing_seconds"],
                   "training_segments": metrics.get("training_segments"), "training_examples": metrics.get("training_examples"),
                   "parameter_count": metrics.get("parameter_count"), "model_bytes": metrics.get("model_bytes"),
                   "timing_repetitions": report.get("hardware", {}).get("timing_repetitions"),
                   "dataset_sha256": report.get("dataset_sha256"), "top_functions": functions[:50],
                   "selected_ml_functions": [item for item in functions if Path(item["file"]).name == "ml.py"]}
            output["runs"].append(row)
            print(json.dumps({key: row[key] for key in ("topology_id", "elapsed_seconds", "reported_training_seconds", "reported_preprocessing_seconds", "other_wall_seconds")}), flush=True)
        except Exception as exc:
            output["runs"].append({"topology_id": topology, "status": "failed", "error": str(exc)})
            print(json.dumps({"topology_id": topology, "status": "failed", "error": str(exc)}), flush=True)
        finally:
            for name, original in original_functions.items():
                setattr(ml, name, original)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 1 if any(run["status"] == "failed" for run in output["runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
