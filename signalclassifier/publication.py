"""Opt-in public paper evidence, rebuilt from explicit typed projections.

Private report ZIPs and existing CSVs must never enter this path. No local Git
commands are used: publishing updates one GitHub branch with a non-forced ref
update after creating immutable blobs, a tree and one atomic commit.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import numbers
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REPOSITORY = "schildtaero/SignalClassifier"
DEFAULT_BRANCH = "experiment-results"
MAX_PACKAGE_BYTES = 16 * 1024**2
MAX_FILE_BYTES = 8 * 1024**2
PACKAGE_FILES = frozenset({"README.md", "experiment.json", "dataset.json", "events-summary.json", "manifest.json"})
_TOKEN_ENV = ("SIGNALCLASSIFIER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_SHA1 = re.compile(r"[0-9a-fA-F]{40}\Z")
_VERSION = re.compile(r"\d{1,3}\.\d{1,3}(?:\.\d{1,3})?(?:(?:a|b|rc|\.post|\.dev)\d+)?(?:\+(?:cpu|cu\d+))?\Z")
_CODES = {"disabled", "invalid_settings", "invalid_package", "unsafe_package", "package_too_large",
          "missing_credentials", "invalid_credentials", "network_error", "api_error", "unauthorized",
          "forbidden", "not_found", "conflict", "invalid_response", "empty_repository", "default_branch_forbidden", "timeout"}
_STATUS = {"queued", "running", "completed", "failed", "cancelled", "interrupted", "timed_out", "skipped", "unvalidated", "evaluated", "unknown"}
_FAMILIES = {"feature_lda", "feature_mlp", "iq_cnn", "spectrogram_cnn", "five_cnn", "fb_lstm", "gru", "resnet", "tcn", "transformer"}
_SOURCES = {"synthetic", "recordings", "recording", "pluto", "rsp1a", "unknown"}
_QUALITY = {"accuracy", "balanced_accuracy", "macro_f1", "brier_score", "ece", "count"}
_RESOURCES = {"training_seconds", "training_wall_seconds", "managed_training_pause_seconds", "coordination_wait_seconds",
              "preprocessing_seconds", "latency_ms_p50", "latency_ms_p95", "model_bytes", "parameter_count", "real_time_factor",
              "real_time_factor_p95", "timing_skipped_model_fraction", "training_segments", "training_examples",
              "reference_latency_ms", "normalized_latency", "training_duration_ratio", "wall_seconds", "sensitivity_snr90_db",
              "process_rss_before_bytes", "process_rss_after_bytes", "sampled_process_rss_max_bytes", "peak_gpu_bytes",
              "search_training_seconds", "search_preprocessing_seconds", "search_elapsed_seconds"}
_PHASES = {"source_loading_seconds", "fingerprint_seconds", "preprocessing_seconds", "device_transfer_seconds", "training_epoch_seconds",
           "validation_seconds", "checkpoint_seconds", "validation_grid_seconds", "cpu_benchmark_seconds", "benchmark_wait_seconds"}
_CONFIG_NUMBERS = {"seed", "samples_per_class", "segment_length", "epochs", "learning_rate", "batch_size", "sample_rate"}
_AUGMENTATION = {"frequency_offset_hz", "sample_clock_ppm", "iq_gain_db", "iq_phase_deg", "dc_offset", "clip_level", "echo_amplitude", "echo_delay_samples"}
_EXECUTION_NUMBERS = {"max_workers", "cpu_threads", "preprocessing_workers", "gpu_workers", "worker_index",
                      "training_batch_size", "validation_batch_size", "training_batches", "validation_batches", "checkpoint_bytes",
                      "loss_host_transfers", "validation_tensor_transfers", "preprocessing_workers_requested", "torch_interop_threads"}
_PUBLIC_CLASSES = {"AM", "FM", "WFM", "NFM", "SSB", "USB", "LSB", "OOK", "ASK", "FSK", "2FSK", "4FSK", "GFSK", "GMSK",
                   "PSK", "BPSK", "QPSK", "8PSK", "D8PSK", "QAM", "16QAM", "64QAM", "256QAM", "1024QAM", "8FSK", "16FSK", "OFDM", "CSS", "pulsed",
                   "POCSAG", "LoRa", "LoRaWAN", "ADS-B", "Mode S", "AIS", "ACARS", "VDL2", "AX.25", "APRS", "DMR", "dPMR",
                   "TETRA", "DAB+", "DVB-T", "DVB-T2", "Wi-Fi", "Bluetooth", "Remote ID", "noise", "unknown", "unbekannt"}
_EVENTS = {"queued", "started", "progress", "completed", "failed", "cancelled", "interrupted", "resources", "resource_sample", "resource_usage",
           "experiment_created", "experiment_started", "experiment_completed", "experiment_failed", "experiment_cancelled", "journal_read_warning"}
_STAGES = {"prepare", "train", "evaluate", "optimize", "final_test", "complete", "execution", "execution_job_complete", "queued", "failed"}


class PublicationError(ValueError):
    def __init__(self, code="api_error"):
        self.code = code if code in _CODES else "api_error"
        super().__init__(f"Paper-Veröffentlichung fehlgeschlagen ({self.code}).")


def safe_error(exc) -> str:
    """Never format exception text, URLs, response bodies or request headers."""
    code = exc.code if isinstance(exc, PublicationError) and exc.code in _CODES else "api_error"
    return f"Paper-Veröffentlichung fehlgeschlagen ({code})."


def _token():
    return next((os.environ[name].strip() for name in _TOKEN_ENV if os.environ.get(name, "").strip()), None)


def credential_status() -> dict:
    present = bool(_token())
    return {"configured": present, "credential_source": "environment" if present else None,
            "default_repository": DEFAULT_REPOSITORY, "default_branch": DEFAULT_BRANCH}


def _valid_branch(value):
    return (isinstance(value, str) and 1 <= len(value) <= 100
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None
            and ".." not in value and "//" not in value and not value.endswith(("/", "."))
            and all(not part.startswith(".") and not part.casefold().endswith(".lock") for part in value.split("/")))


def normalize_publication(config=None) -> dict:
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise PublicationError("invalid_settings")
    supplied = config
    if not isinstance(supplied, dict) or set(supplied) - {"enabled", "repository", "branch"}:
        raise PublicationError("invalid_settings")
    result = {"enabled": False, "repository": DEFAULT_REPOSITORY, "branch": DEFAULT_BRANCH, **supplied}
    repo, branch = result["repository"], result["branch"]
    if type(result["enabled"]) is not bool or not isinstance(repo, str) or len(repo) > 140:
        raise PublicationError("invalid_settings")
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", repo) is None:
        raise PublicationError("invalid_settings")
    if not _valid_branch(branch) or branch.casefold() in {"main", "master"}:
        raise PublicationError("invalid_settings")
    return result


def _obj(value):
    return value if isinstance(value, dict) else {}


def _arr(value):
    return value if isinstance(value, list) else []


def _num(value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) > 1e15:
        return None
    return int(value) if isinstance(value, numbers.Integral) else number


def _numbers(value, keys):
    value = _obj(value)
    return {key: _num(value[key]) for key in sorted(keys) if key in value and _num(value[key]) is not None}


def _bools(value, keys):
    value = _obj(value)
    return {key: value[key] for key in keys if type(value.get(key)) is bool}


def _enum(value, allowed, default="unknown"):
    return value if isinstance(value, str) and value in allowed else default


def _digest(value):
    return value.lower() if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def _error_category(value):
    text = str(value or "").lower()
    if not text:
        return None
    if any(x in text for x in ("vor start", "before start", "vor jobstart")):
        return "budget_before_start"
    if any(x in text for x in ("zeitbudget", "timeout", "deadline")):
        return "time_budget"
    if "out of memory" in text or "outofmemory" in text:
        return "out_of_memory"
    if any(x in text for x in ("nan", "nonfinite", "nichtendlich")):
        return "numerical_error"
    if any(x in text for x in ("filenotfound", "datei fehlt", "no such file")):
        return "missing_artifact"
    return "trial_error"


class _Projection:
    def __init__(self, experiment_id):
        from .topologies import list_topologies
        self.salt = str(experiment_id or "unspecified")[:4096]
        self.omissions = Counter()
        self.known_models = {x["id"] for x in list_topologies()}

    def alias(self, kind, value):
        if value is None:
            return None
        raw = f"signalclassifier-paper-v1\0{self.salt}\0{kind}\0{str(value)[:4096]}".encode()
        return f"{kind}-" + hashlib.sha256(raw).hexdigest()[:24]

    def label(self, value):
        return value if isinstance(value, str) and value in _PUBLIC_CLASSES else self.alias("class", value)

    def bounded(self, value, limit, kind):
        value = _arr(value)
        self.omissions[kind] += max(0, len(value) - limit)
        return value[:limit]

    def classes(self, value):
        return [self.label(x) for x in self.bounded(value, 64, "classes") if isinstance(x, str)]

    def model_id(self, value):
        # Only shipped identifiers retain their names. User-created names are
        # opaque even when they happen to match a safe filename regex.
        return value if isinstance(value, str) and value in self.known_models else self.alias("model", value)

    def config(self, value):
        value = _obj(value)
        out = _numbers(value, _CONFIG_NUMBERS)
        out.update(_bools(value, ("include_clean", "use_priors")))
        out["source"] = _enum(value.get("source"), _SOURCES)
        out["evaluation_mode"] = _enum(value.get("evaluation_mode"), {"independent", "training_only"})
        out["device"] = _enum(value.get("device"), {"auto", "cpu", "cuda"})
        for key in ("segment_lengths", "learning_rates", "batch_sizes", "snr_db", "jitter"):
            if isinstance(value.get(key), list):
                out[key] = [n for x in self.bounded(value[key], 32, key) if (n := _num(x)) is not None]
        out["classes"] = self.classes(value.get("classes"))
        out["augmentation"] = _numbers(value.get("augmentation"), _AUGMENTATION)
        execution = _obj(value.get("execution"))
        out["execution"] = {**_numbers(execution, _EXECUTION_NUMBERS), "mode": _enum(execution.get("mode"), {"auto", "serial", "parallel"})}
        return out

    def quality(self, value, allowed=True):
        if not allowed:
            return None
        value = _obj(value); out = _numbers(value, _QUALITY)
        for key in ("accuracy", "balanced_accuracy", "macro_f1", "ece"):
            if key in out and not 0 <= out[key] <= 1:
                del out[key]
        ci = _arr(value.get("accuracy_ci95"))
        if len(ci) == 2 and all(_num(x) is not None and 0 <= x <= 1 for x in ci):
            out["accuracy_ci95"] = [float(x) for x in ci]
        matrix = _arr(value.get("confusion_matrix"))
        if len(matrix) <= 64 and all(isinstance(row, list) and len(row) <= 64 and all(_num(x) is not None and x >= 0 for x in row) for row in matrix):
            out["confusion_matrix"] = [[_num(x) for x in row] for row in matrix]
        out["recall"] = {self.label(key): float(number) for key, value in list(_obj(value.get("recall")).items())[:64]
                         if (number := _num(value)) is not None and 0 <= number <= 1}
        return out

    def history(self, value):
        return [_numbers(point, {"epoch", "epoch_count", "training_loss", "validation_loss"})
                for point in self.bounded(value, 100, "epochs") if isinstance(point, dict)]

    def score(self, value, allowed=True):
        if not allowed or not isinstance(value, dict):
            return None
        out = {**_numbers(value, {"total"}), **_bools(value, ("eligible_for_live", "quality_score_available"))}
        components = _obj(value.get("components"))
        out["components"] = {key: _numbers(components.get(key), {"points", "weight", "normalized", "raw"})
                             for key in ("accuracy", "robustness", "realtime", "training") if key in components}
        out["weights"] = _numbers(value.get("weights"), {"accuracy", "robustness", "realtime", "training"})
        out["parameters"] = _numbers(value.get("parameters"), {"robustness_mean_weight", "robustness_worst_weight", "full_realtime_credit_speedup", "training_reference_seconds", "chance_level", "num_classes"})
        return out

    def grid(self, value, split, allowed=True):
        if not allowed:
            return []
        return [{"split": split, **_numbers(row, {"candidate", "trial", "segment_length", "snr_db", "jitter"}),
                 "metrics": self.quality(row)} for row in self.bounded(value, 128, "grid_cells") if isinstance(row, dict)]

    def splits(self, value):
        value = _obj(value)
        return {key: [self.alias("group", x) for x in self.bounded(value.get(key), 1000, "split_groups")]
                for key in ("train", "validation", "test")}

    def usage(self, value):
        out = {}
        for split in ("train", "validation", "test"):
            row = _obj(_obj(value).get(split))
            out[split] = _numbers(row, {"segments", "groups", "signal_seconds", "reference_window_seconds"})
            out[split]["sample_rates"] = [n for x in self.bounded(row.get("sample_rates"), 1000, "sample_rates") if (n := _num(x)) is not None]
        return out

    def training(self, value):
        value = _obj(value)
        return {**_numbers(value, _EXECUTION_NUMBERS), **_bools(value, ("process_isolated", "external_host_load_verified")),
                "device": _enum(value.get("device"), {"cpu", "cuda", "cuda:0"}),
                "checkpoint_device": _enum(value.get("checkpoint_device"), {"cpu", "cuda", "cuda:0", "mixed"})}

    def candidates(self, value, report, allow_quality, allow_test, optimization):
        out = []
        for row in self.bounded(value, 50, "candidates"):
            row = _obj(row); candidate = _num(row.get("candidate"))
            if optimization.get("enabled") is True and _arr(optimization.get("trials")):
                # Every HPO candidate already has its complete typed entry below;
                # publishing it a second time wastes the public byte budget.
                out.append({"candidate": candidate, "optimization_trial": _num(row.get("optimization_trial", candidate)),
                            "source": "optimization.trials_by_trial_number"})
                continue
            out.append({"candidate": candidate, "config": _numbers(row, {"segment_length", "learning_rate", "batch_size", "epochs"}),
                "validation": self.quality(row.get("validation"), allow_quality), "score": self.score(row.get("score"), allow_quality),
                "resource_metrics": _numbers(row, _RESOURCES), "history": self.history(row.get("history")),
                "training_execution": self.training(row.get("training_execution")), "phase_timings": _numbers(row.get("phase_timings"), _PHASES),
                "data_usage": self.usage(row.get("data_usage")), "dataset_sha256": _digest(row.get("dataset_sha256")),
                "validation_robustness": self.grid(row.get("validation_trials"), "validation", allow_quality),
                "test_robustness": self.grid([point for point in _arr(report.get("trials")) if _obj(point).get("candidate") == candidate], "test", allow_test)})
        return out

    def optimization(self, value, allow_quality):
        value = _obj(value)
        out = {**_numbers(value, {"objective_version", "max_trials", "timeout_seconds", "reference_window_samples", "planned_trials", "completed_trials", "failed_trials", "timed_out_trials", "skipped_trials", "best_trial", "test_evaluations", "total_training_seconds", "total_preprocessing_seconds", "elapsed_seconds", "search_elapsed_seconds", "final_test_seconds"}),
               **_bools(value, ("enabled",)), "method": _enum(value.get("method"), {"random"}),
               "objective": _enum(value.get("objective"), {"score", "macro_f1", "balanced_accuracy"}),
               "status": _enum(value.get("status"), _STATUS),
               "stop_reason": _enum(value.get("stop_reason"), {"timeout", "max_trials", "search_space_exhausted", "user_cancellation", "interrupted", "all_trials_failed"}),
               "best_config": _numbers(value.get("best_config"), {"segment_length", "learning_rate", "batch_size", "epochs"})}
        space = _obj(value.get("search_space"))
        out["search_space"] = _numbers(space, {"learning_rate_min", "learning_rate_max"})
        for key in ("segment_lengths", "batch_sizes", "epochs"):
            out["search_space"][key] = [n for x in self.bounded(space.get(key), 8, "search_choices") if (n := _num(x)) is not None]
        out["excluded_segment_lengths"] = [{**_numbers(row, {"segment_length"}), "exclusion": "dataset_constraints"}
                                          for row in self.bounded(value.get("excluded_segment_lengths"), 8, "excluded_lengths")]
        out["trials"] = []
        for trial in self.bounded(value.get("trials"), 50, "hpo_trials"):
            trial = _obj(trial); sub = _obj(trial.get("report")); evaluated = allow_quality and trial.get("status") == "completed"
            out["trials"].append({**_numbers(trial, {"trial", "elapsed_seconds", "partial_training_seconds", "partial_preprocessing_seconds"}),
                "objective_value": _num(trial.get("objective_value")) if evaluated else None,
                "status": _enum(trial.get("status"), _STATUS), "error_category": _error_category(trial.get("error")),
                "config": _numbers(trial.get("config"), {"segment_length", "learning_rate", "batch_size", "epochs"}),
                "validation": self.quality(trial.get("validation", sub.get("validation")), evaluated),
                "score": self.score(trial.get("score", sub.get("score")), evaluated),
                "resource_metrics": _numbers(trial.get("metrics"), _RESOURCES),
                "epoch_history": self.history(trial.get("epoch_history", sub.get("history"))),
                "training_execution": self.training(trial.get("training_execution", sub.get("training_execution"))),
                "phase_timings": _numbers(trial.get("phase_timings", sub.get("phase_timings")), _PHASES),
                "data_usage": self.usage(sub.get("data_usage")), "split_groups": self.splits(sub.get("split_groups")),
                "dataset_sha256": _digest(sub.get("dataset_sha256")),
                "evaluation_scope": _enum(sub.get("evaluation_scope"), {"validation_only", "validation_and_test", "training_only"}),
                "final_test_evaluations": _num(sub.get("final_test_evaluations")),
                "validation_robustness": self.grid(sub.get("validation_trials"), "validation", evaluated)})
        return out

    def model(self, entry, root_config, optimization_only=False):
        entry = _obj(entry); report = _obj(entry.get("report")) or entry
        topology = _obj(report.get("topology")); ident = entry.get("topology_id") or topology.get("id") or report.get("topology_id")
        if not topology:
            topology = next((_obj(t) for t in _arr(root_config.get("topologies")) if _obj(t).get("id") == ident), {})
            if not topology and _obj(root_config.get("topology")).get("id") == ident:
                topology = root_config["topology"]
        config = _obj(report.get("config")) or root_config
        allow = config.get("evaluation_mode") != "training_only" and report.get("evaluation_available") is not False and report.get("validation_status") != "unvalidated"
        test = allow and report.get("test_evaluation_available") is True and report.get("evaluation_scope") == "validation_and_test"
        optimization = report if optimization_only else _obj(report.get("optimization"))
        return {"public_id": self.model_id(ident), "topology": {"public_id": self.model_id(ident), "family": _enum(topology.get("family"), _FAMILIES),
                    "input": _enum(topology.get("input"), {"features", "feature_sequence", "iq", "spectrogram"}),
                    "parameters": _numbers(topology.get("parameters"), {"width", "depth", "kernel", "heads", "dropout"})},
                "config": self.config(config), "classes": self.classes(report.get("classes", config.get("classes"))),
                "evaluation_scope": _enum(report.get("evaluation_scope"), {"validation_only", "validation_and_test", "training_only"}),
                **_bools(report, ("evaluation_available", "test_evaluation_available")),
                "final_test_evaluations": _num(report.get("final_test_evaluations")),
                "validation": self.quality(entry.get("validation", report.get("validation")), allow),
                "test": self.quality(report.get("metrics"), test), "resource_metrics": _numbers(report.get("metrics"), _RESOURCES),
                "score": self.score(entry.get("score", report.get("score")), allow),
                "score_definition": _score_definition(report.get("score_definition", optimization.get("score_definition"))),
                "score_rank": _num(entry.get("score_rank")) if allow else None,
                "accuracy_rank": _num(entry.get("accuracy_rank")) if allow else None,
                "selected_candidate": _num(report.get("selected_candidate")),
                "candidates": self.candidates(report.get("candidates"), report, allow, test, optimization),
                "history": self.history(report.get("history")), "validation_robustness": self.grid(report.get("validation_trials"), "validation", allow),
                "test_robustness": self.grid(report.get("trials"), "test", test), "split_groups": self.splits(report.get("split_groups", optimization.get("split_groups"))),
                "data_usage": self.usage(report.get("data_usage")), "dataset_sha256": _digest(report.get("dataset_sha256")),
                "dataset_hash_scope": "used_segment_fingerprints_including_original_group_label_rate; not_full_iq_file",
                "test_dataset_sha256": _digest(report.get("test_dataset_sha256")),
                "training_execution": self.training(report.get("training_execution")), "phase_timings": _numbers(report.get("phase_timings"), _PHASES),
                "hardware": _hardware(report.get("hardware")), "execution": _execution(report.get("execution")),
                "benchmark": _benchmark(report.get("benchmark"), self),
                "optimization": self.optimization(optimization, allow)}


def _benchmark(value, projection):
    value = _obj(value)
    known = "signalclassifier-features-v1/AM4096-fs256000-seed17"
    return {**_numbers(value, {"reference_latency_ms", "p95_ms", "warmup", "repetitions", "batch_size", "benchmark_wait_seconds"}),
            "reference_id": known if value.get("reference") == known else "unknown",
            "reference_alias": projection.alias("reference", value.get("reference")) if value.get("reference") != known else None}


def _resource_summary(value):
    value = _obj(value)
    cpu_keys = ("system_percent", "process_percent", "app_system_percent")
    gpu_keys = ("utilization_percent", "memory_used_bytes", "memory_total_bytes")
    return {**_numbers(value, {"sample_count"}),
            "cpu": {key: _numbers(_obj(value.get("cpu")).get(key), {"mean", "maximum", "sample_count"}) for key in cpu_keys},
            "gpu_devices": [{**_numbers(row, {"index"}), **{key: _numbers(_obj(row).get(key), {"mean", "maximum", "sample_count"}) for key in gpu_keys}}
                            for row in _arr(value.get("gpu_devices"))[:16] if isinstance(row, dict)],
            "cpu_scope": "application_process_tree_one_logical_cpu_100_percent", "gpu_scope": "whole_device_including_other_programs",
            "sampling_scope": "periodic_samples_missing_values_not_zero"}


def _resource_sample(value, elapsed):
    value = _obj(value); cpu = _obj(value.get("cpu")); gpu = _obj(value.get("gpu"))
    return {"elapsed_seconds": _num(elapsed), "interval_seconds": _num(value.get("interval_seconds")),
            "stale": value.get("stale") if type(value.get("stale")) is bool else None,
            "sampler_error_present": bool(value.get("sampler_error")),
            "cpu": {**{key: _num(cpu.get(key)) for key in ("system_percent", "process_percent", "app_system_percent", "process_tree_rss_bytes", "memory_total_bytes", "memory_available_bytes", "normalization_cpu_capacity", "logical_cpus", "process_count", "warming_processes")},
                    "scope": "application_process_tree_one_logical_cpu_100_percent", "rss_scope": "process_sum_may_double_count_shared_pages"},
            "gpu": {"available": gpu.get("available") if type(gpu.get("available")) is bool else None,
                    "scope": "whole_device_including_other_programs",
                    "devices": [{key: _num(row.get(key)) for key in ("index", "utilization_percent", "memory_used_bytes", "memory_total_bytes")}
                                for row in _arr(gpu.get("devices"))[:16] if isinstance(row, dict)]}}


def _execution(value):
    value = _obj(value)
    keys = _EXECUTION_NUMBERS | {"cpu_budget", "ram_budget_bytes", "estimated_ram_per_worker_bytes", "gpu_memory_budget_bytes", "estimated_gpu_per_worker_bytes"}
    return {**_numbers(value, {"wall_seconds", "peak_active_workers", "spawned_processes", "completed_jobs", "failed_jobs", "timed_out_jobs", "cancelled_jobs"}),
            **_bools(value, ("worker_reuse", "nested_parallelism_disabled", "external_host_load_verified")),
            "axis": _enum(value.get("axis"), {"topologies", "trials"}),
            "requested": {**_numbers(value.get("requested"), keys), "mode": _enum(_obj(value.get("requested")).get("mode"), {"auto", "serial", "parallel"})},
            "resolved": {**_numbers(value.get("resolved"), keys), "mode": _enum(_obj(value.get("resolved")).get("mode"), {"serial", "parallel"})}}


def _score_definition(value):
    value = _obj(value)
    weights = _numbers(value.get("weights"), {"accuracy", "robustness", "realtime", "training"})
    recognized = value.get("version") == 1 and weights == {"accuracy": 45, "robustness": 35, "realtime": 15, "training": 5}
    return {**_numbers(value, {"version", "maximum"}), "weights": weights,
            "parameters": _numbers(value.get("parameters"), {"robustness_mean_weight", "robustness_worst_weight", "full_realtime_credit_speedup", "training_reference_seconds"}),
            "formula_id": "signalclassifier-score-v1" if recognized else "unrecognized_score_definition",
            "quality_source": "validation_only", "hardware_scope": "measured_host_not_universal_prediction"}


def _hardware(value):
    value = _obj(value)
    out = _numbers(value, {"logical_cpus", "physical_cpus", "ram_bytes", "available_ram_bytes", "gpu_memory_bytes", "torch_threads", "batch_size", "inference_batch_size", "warmup", "timing_repetitions"})
    for key in ("training_device", "inference_device", "device"):
        out[key] = _enum(value.get(key), {"cpu", "cuda", "cuda:0"})
    # Extract bounded product identifiers, not arbitrary hostname-bearing text.
    cpu = re.sub(r"\(R\)|\(TM\)", "", str(value.get("cpu", "")), flags=re.I)
    match = re.search(r"\b(Xeon\s+(?:[A-Z]-\d{4}|[A-Z]?\d{4}[A-Z]?)|Ryzen\s+[3579]\s+\d{4,5}[A-Z]{0,3}|(?:Core\s+)?i[3579]-\d{4,5}[A-Z]{0,3}|EPYC\s+\d{4}[A-Z]?)\b", cpu, re.I)
    out["cpu_model"] = match.group(0) if match else None
    gpu = str(value.get("gpu", ""))
    match = re.search(r"\b((?:GeForce\s+)?(?:RTX|GTX)\s+(?:A)?\d{3,4}(?:\s+Ti)?|Radeon\s+RX\s+\d{3,4}(?:\s+XT)?)\b", gpu, re.I)
    out["gpu_model"] = match.group(0) if match else None
    platform = str(value.get("platform", "")).lower()
    out["platform_family"] = "linux-wsl2" if "microsoft-standard" in platform else "linux" if "linux" in platform else "windows" if "windows" in platform else "unknown"
    out["memory_scope"] = "sampled_process_rss_including_framework_not_isolated_model_peak"
    out["timing_scope"] = "reported_host_measurement_not_hardware_independent"
    return out


def _software(value):
    value = _obj(value); out = {"application": "SignalClassifier"}
    for key in ("version", "python"):
        raw = value.get(key)
        out[key] = raw if isinstance(raw, str) and _VERSION.fullmatch(raw) else None
    revision = value.get("git_revision")
    out["git_revision"] = revision.lower() if isinstance(revision, str) and _SHA1.fullmatch(revision) else None
    out.update(_bools(value, ("working_tree_dirty",)))
    out["dependencies"] = {key: version for key in ("numpy", "scipy", "torch", "fastapi", "uvicorn", "psutil", "python-multipart")
                           if isinstance(version := _obj(value.get("dependencies")).get(key), str) and _VERSION.fullmatch(version)}
    known_files = {name + ".py" for name in ("__init__", "__main__", "app", "db", "drones", "experiment_log", "labels", "ml", "optimization", "receivers", "reporting", "sigmf", "topologies", "execution", "telemetry", "decision", "publication", "analysis_runtime", "surveillance")}
    out["source_files_sha256"] = {key: digest for key, raw in _obj(value.get("source_files_sha256")).items() if key in known_files and (digest := _digest(raw))}
    return out


def _views(experiment):
    report = _obj(experiment.get("report")); entries = _arr(report.get("comparison"))
    if entries:
        return [(row, False) for row in entries]
    views, seen = [], set()
    if report:
        views.append((report, False)); seen.add(_obj(report.get("topology")).get("id"))
    partials = _arr(experiment.get("partial_reports"))
    for kind in ("report", "optimization"):
        for part in partials:
            part = _obj(part); row = _obj(part.get("report"))
            if part.get("kind") != kind:
                continue
            ident = row.get("topology_id") or _obj(row.get("topology")).get("id")
            if ident in seen:
                continue
            seen.add(ident); views.append((row, kind == "optimization"))
    if not views and any(key in experiment for key in ("metrics", "optimization", "comparison")):
        return [(experiment, False)]
    return views


def make_publication_package(experiment, dataset_inventory=None, events=None) -> dict[str, bytes]:
    if not isinstance(experiment, dict):
        raise PublicationError("invalid_package")
    projection = _Projection(experiment.get("id")); config = _obj(experiment.get("config")); report = _obj(experiment.get("report"))
    views = _views(experiment); projection.omissions["models"] += max(0, len(views) - 64)
    data = {"schema_version": 1, "package_kind": "signalclassifier-paper-results", "run_alias": projection.alias("run", experiment.get("id")),
            "status": _enum(experiment.get("status"), _STATUS), "error_category": _error_category(experiment.get("error")),
            "config": projection.config(config), "overall_comparison_available": bool(report.get("comparison")),
            "models": [projection.model(row, config, optimization_only=partial) for row, partial in views[:64]],
            "software": _software(_obj(experiment.get("provenance")).get("software")),
            "hardware_at_queue": _hardware(_obj(experiment.get("provenance")).get("hardware_at_queue")),
            "execution": _execution(report.get("execution")),
            "resource_telemetry": _resource_summary(report.get("resource_telemetry")),
            "selection_objective": _enum(report.get("selection_objective"), {"score", "macro_f1", "balanced_accuracy"}),
            "score_definition": _score_definition(report.get("score_definition")),
            "selected_topology_id": projection.model_id(report.get("selected_topology_id")) if report.get("selected_topology_id") else None,
            "recommended_topology_ids": [projection.model_id(x) for x in _arr(report.get("recommended_topology_ids"))[:3]],
            "interpretation": {"quality_ranking_source": "validation_only", "final_test_separate": True,
                "transport_source_does_not_verify_rf_origin": True, "scores_are_not_universal_hardware_predictions": True}}
    try:
        start, end = datetime.fromisoformat(experiment["started_at"]), datetime.fromisoformat(experiment["finished_at"])
        data["wall_seconds"] = max(0., (end - start).total_seconds())
    except (KeyError, ValueError, TypeError, OverflowError):
        data["wall_seconds"] = None
    inventory = []
    for row in projection.bounded(dataset_inventory, 1000, "recordings"):
        row = _obj(row)
        inventory.append({"recording_alias": projection.alias("recording", row.get("id")), "group_alias": projection.alias("group", row.get("group_id")),
                          "label": projection.label(row.get("label")), **_bools(row, ("verified", "synthetic")),
                          **_numbers(row, {"sample_rate", "center_frequency_hz", "sample_count", "duration_seconds", "data_bytes"}),
                          "declared_source": _enum(row.get("source"), _SOURCES),
                          "metadata_sha256": _digest(row.get("metadata_sha256")),
                          "iq_full_content_sha256": _digest(row.get("iq_full_content_sha256")),
                          "hash_scope": "original_metadata_or_explicit_full_iq_hash_only_when_present",
                          "integrity": {**_numbers(row.get("capture_integrity"), {"reported_gaps", "overflows"}), **_bools(row.get("capture_integrity"), ("gap_free_verified",))}})
    dataset = {"schema_version": 1, "recordings": inventory, "recording_count": len(inventory),
               "group_count": len({row["group_alias"] for row in inventory if row["group_alias"]}),
               "pseudonym_scope": "SHA256(namespace, internal run id, kind, internal id); stable within run, not anonymization proof",
               "physical_rf_origin_verified": False}
    event_counts = Counter(); progress = []; resources = []
    for row in projection.bounded(events, 100000, "events"):
        row = _obj(row); event_counts[_enum(row.get("event"), _EVENTS, "other")] += 1
        payload = _obj(row.get("data"))
        numeric = _numbers(payload, {"epoch", "epoch_count", "training_loss", "validation_loss", "optimization_trial", "training_elapsed_seconds", "preprocessing_seconds"})
        if numeric and len(progress) < 10000:
            progress.append({**numeric, "stage": _enum(payload.get("stage"), _STAGES), "model_alias": projection.model_id(payload.get("topology_id")) if payload.get("topology_id") else None})
        elif numeric:
            projection.omissions["numeric_events"] += 1
        if row.get("event") in ("resources", "resource_sample", "resource_usage"):
            if len(resources) < 10000:
                resources.append(_resource_sample(payload, row.get("elapsed_seconds")))
            else:
                projection.omissions["resource_events"] += 1
    event_summary = {"schema_version": 1, "event_counts": dict(event_counts), "numeric_progress": progress, "resource_samples": resources,
                     "raw_events_included": False}
    data["omitted_counts"] = dict(projection.omissions)
    files = {"experiment.json": _encoded(data), "dataset.json": _encoded(dataset), "events-summary.json": _encoded(event_summary),
             "README.md": b"# SignalClassifier public paper evidence\n\nOpt-in, typed projections of methods and results. Validation and final test remain separate.\nNo IQ, weights, private paths, free-text notes, raw metadata, raw events or credentials are included.\nKnown class codes retain their names; custom classes and identifiers are pseudonyms. Their local mapping is not published.\nHPO candidates reference optimization.trials by optimization_trial == trial; full metrics and curves are stored there once. Manual candidates retain their own curves and metrics.\nRecording transport does not certify real RF origin. Group aliases preserve within-run split relationships.\nMissing data and omitted counts are explicit. This package is not a full private report bundle.\n"}
    files["manifest.json"] = _encoded({"schema_version": 1, "package_kind": "signalclassifier-paper-results", "projection_version": 1,
        "contains_iq": False, "contains_weights": False,
        "files": {name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in sorted(files.items())}})
    _check_package(files)
    return files


def _check_package(files):
    if not isinstance(files, dict) or set(files) != PACKAGE_FILES or any(not isinstance(value, bytes) for value in files.values()):
        raise PublicationError("invalid_package")
    if any(len(value) > MAX_FILE_BYTES for value in files.values()) or sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
        raise PublicationError("package_too_large")
    try:
        manifest = json.loads(files["manifest.json"])
        if manifest.get("package_kind") != "signalclassifier-paper-results" or set(manifest["files"]) != PACKAGE_FILES - {"manifest.json"}:
            raise PublicationError("invalid_package")
        for name, description in manifest["files"].items():
            if description != {"bytes": len(files[name]), "sha256": hashlib.sha256(files[name]).hexdigest()}:
                raise PublicationError("invalid_package")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PublicationError("invalid_package") from None
    for name in _TOKEN_ENV:
        token = os.environ.get(name, "").strip()
        if len(token) >= 8 and any(token.encode() in value for value in files.values()):
            raise PublicationError("unsafe_package")
    if any(re.search(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", value) for value in files.values()):
        raise PublicationError("unsafe_package")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _GitHub:
    def __init__(self, token):
        self.token = token
        self.deadline = time.monotonic() + 120
        self.calls = 0
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, data=None):
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.calls > 40:
            raise PublicationError("timeout")
        body = _encoded(data) if data is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "SignalClassifier-Paper-Publication/1"})
        try:
            with self.opener.open(request, timeout=min(15., remaining)) as response:
                content = response.read(2 * 1024**2 + 1)
            if len(content) > 2 * 1024**2:
                raise PublicationError("invalid_response")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise PublicationError("invalid_response")
            return result
        except urllib.error.HTTPError as exc:
            code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "conflict"}.get(exc.code, "api_error")
            raise PublicationError(code) from None
        except (OSError, TimeoutError, urllib.error.URLError):
            raise PublicationError("network_error") from None
        except (ValueError, UnicodeError):
            raise PublicationError("invalid_response") from None


def _sha(value):
    if not isinstance(value, str) or not _SHA1.fullmatch(value):
        raise PublicationError("invalid_response")
    return value.lower()


def publish_package(publication, experiment_id, files) -> dict:
    settings = normalize_publication(publication)
    if not settings["enabled"]:
        return {"status": "disabled"}
    if not isinstance(experiment_id, str) or re.fullmatch(r"[a-fA-F0-9]{32}", experiment_id) is None:
        raise PublicationError("invalid_package")
    _check_package(files)
    token = _token()
    if not token:
        raise PublicationError("missing_credentials")
    if not 8 <= len(token) <= 4096 or not token.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise PublicationError("invalid_credentials")
    repository, branch = settings["repository"], settings["branch"]
    api = _GitHub(token); base = "/repos/" + repository
    ref_path = base + "/git/ref/heads/" + urllib.parse.quote(branch, safe="")
    update_path = base + "/git/refs/heads/" + urllib.parse.quote(branch, safe="")
    directory = "paper-results/" + experiment_id.lower()
    try:
        repo = api.request("GET", base)
        default = repo.get("default_branch")
        if not _valid_branch(default):
            raise PublicationError("invalid_response")
        if branch == default:
            raise PublicationError("default_branch_forbidden")
        try:
            head = _sha(api.request("GET", ref_path)["object"]["sha"])
        except PublicationError as exc:
            if exc.code != "not_found":
                raise
            try:
                initial = _sha(api.request("GET", base + "/git/ref/heads/" + urllib.parse.quote(default, safe=""))["object"]["sha"])
            except PublicationError as error:
                raise PublicationError("empty_repository" if error.code == "not_found" else error.code) from None
            try:
                head = _sha(api.request("POST", base + "/git/refs", {"ref": "refs/heads/" + branch, "sha": initial})["object"]["sha"])
            except PublicationError as error:
                if error.code != "conflict":
                    raise
                head = _sha(api.request("GET", ref_path)["object"]["sha"])
        blobs = []
        for name, content in sorted(files.items()):
            blob = api.request("POST", base + "/git/blobs", {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
            blobs.append({"path": directory + "/" + name, "mode": "100644", "type": "blob", "sha": _sha(blob.get("sha"))})
        for attempt in range(3):
            commit = api.request("GET", base + "/git/commits/" + head)
            old_tree = _sha(commit["tree"]["sha"])
            tree = _sha(api.request("POST", base + "/git/trees", {"base_tree": old_tree, "tree": blobs})["sha"])
            already_exists = tree == old_tree
            if already_exists:
                published = head
            else:
                published = _sha(api.request("POST", base + "/git/commits", {"message": "Publish sanitized experiment " + experiment_id.lower(), "tree": tree, "parents": [head]})["sha"])
                try:
                    api.request("PATCH", update_path, {"sha": published, "force": False})
                except PublicationError as error:
                    if error.code != "conflict" or attempt == 2:
                        raise
                    head = _sha(api.request("GET", ref_path)["object"]["sha"])
                    continue
            return {"status": "published", "repository": repository, "branch": branch, "commit_sha": published,
                    "url": f"https://github.com/{repository}/commit/{published}", "directory": directory,
                    "files": [item["path"] for item in blobs], "file_count": len(blobs), "total_bytes": sum(map(len, files.values())),
                    "already_exists": already_exists}
        raise PublicationError("conflict")
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("api_error") from None
