"""Cached, low-rate host/process-tree and NVIDIA device telemetry."""
from __future__ import annotations

import copy
import csv
import io
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from .db import utcnow
from .execution import effective_cpu_capacity


def _number(value, minimum=0, maximum=None):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        return None
    return result


def parse_nvidia_csv(output: str) -> list[dict]:
    devices = []
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if len(row) != 5:
            continue
        index = _number(row[0], maximum=1024)
        if index is None or not index.is_integer():
            continue
        used, total = _number(row[3]), _number(row[4])
        devices.append({"index": int(index), "name": row[1].strip(),
                        "utilization_percent": _number(row[2], maximum=100),
                        "memory_used_bytes": int(used * 1024**2) if used is not None else None,
                        "memory_total_bytes": int(total * 1024**2) if total is not None else None})
    return devices


def nvidia_sample(executable: str | None) -> dict:
    result = {"available": False, "provider": "nvidia-smi", "scope": "whole_device", "devices": [],
              "reason": None, "scope_note": "Gesamtes NVIDIA-Gerät einschließlich anderer Programme; keine prozessbezogene CUDA-Auslastung."}
    if not executable:
        result["reason"] = "nvidia-smi nicht verfügbar; CUDA-Training kann unabhängig davon verfügbar sein."
        return result
    try:
        query = subprocess.run([executable, "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=1.5, check=False)
        if query.returncode:
            result["reason"] = (query.stderr or query.stdout or "GPU-Abfrage fehlgeschlagen").strip()[:500]
            return result
        result["devices"] = parse_nvidia_csv(query.stdout)
        result["available"] = bool(result["devices"])
        if not result["available"]:
            result["reason"] = "Keine auswertbaren NVIDIA-Gerätedaten erhalten."
        elif any(device["utilization_percent"] is None for device in result["devices"]):
            result["reason"] = "GPU erkannt; einzelne Auslastungswerte werden vom Treiber/WSL nicht bereitgestellt."
    except (OSError, subprocess.SubprocessError) as exc:
        result["reason"] = str(exc)[:500]
    return result


class ResourceSummary:
    def __init__(self):
        self.samples = 0
        self.first = None
        self.last = None
        self.cpu = {}
        self.devices = {}

    @staticmethod
    def _add(target, key, value):
        measured = _number(value)
        if measured is not None:
            stats = target.setdefault(key, {"sum": 0., "count": 0, "maximum": 0.})
            stats.update(sum=stats["sum"] + measured, count=stats["count"] + 1, maximum=max(stats["maximum"], measured))

    def add(self, sample):
        self.samples += 1
        self.first = self.first or sample.get("sampled_at")
        self.last = sample.get("sampled_at")
        for key in ("system_percent", "process_percent", "app_system_percent"):
            self._add(self.cpu, key, sample.get("cpu", {}).get(key))
        for device in sample.get("gpu", {}).get("devices", []):
            stats = self.devices.setdefault(str(device["index"]), {"index": device["index"], "name": device.get("name"), "values": {}})
            for key in ("utilization_percent", "memory_used_bytes", "memory_total_bytes"):
                self._add(stats["values"], key, device.get(key))

    @staticmethod
    def _values(stats):
        return {key: {"mean": values["sum"] / values["count"], "maximum": values["maximum"], "sample_count": values["count"]}
                for key, values in stats.items()}

    def snapshot(self):
        return {"sample_count": self.samples, "first_sample_utc": self.first, "last_sample_utc": self.last,
                "cpu": self._values(self.cpu),
                "gpu_devices": [{"index": stats["index"], "name": stats["name"], **self._values(stats["values"])} for stats in self.devices.values()],
                "scope": "Periodic samples across the whole experiment including CPU phases and waits. CPU app includes child workers; GPU is whole-device including other programs.",
                "sampling_note": "Short GPU bursts can fall between samples; missing values have not been replaced with zero."}


class ResourceTelemetry:
    """Sample outside HTTP/training threads; missing values remain unknown."""
    def __init__(self, interval_seconds=2., on_sample=None):
        self.interval_seconds = float(interval_seconds)
        if not .1 <= self.interval_seconds <= 60:
            raise ValueError("Telemetry interval must be between 0.1 and 60 seconds")
        self.on_sample = on_sample
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_sample = None
        self._processes = {}
        self._system_primed = False
        self._parent = psutil.Process()
        self._nvidia = shutil.which("nvidia-smi")
        if self._nvidia is None and Path("/usr/lib/wsl/lib/nvidia-smi").is_file():
            self._nvidia = "/usr/lib/wsl/lib/nvidia-smi"
        self._latest = {"sampled_at": None, "interval_seconds": self.interval_seconds,
                        "cpu": {"system_percent": None, "process_percent": None, "app_system_percent": None,
                                "logical_cpus": os.cpu_count(), "process_count": 0,
                                "process_percent_scope": "application_process_tree_one_logical_cpu=100%"},
                        "gpu": {"available": False, "provider": "nvidia-smi", "scope": "whole_device",
                                "devices": [], "reason": "Erste Messung steht aus."}, "sampler_error": None}
        self._thread = threading.Thread(target=self._run, daemon=True, name="resource-telemetry")
        self._thread.start()

    def _cpu_sample(self):
        system = psutil.cpu_percent(interval=None)
        system = _number(system, maximum=100) if self._system_primed else None
        self._system_primed = True
        try:
            current = [self._parent, *self._parent.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            current = [self._parent]
        retained, percentages, rss, warming = {}, [], 0, 0
        for process in current:
            try:
                key = (process.pid, process.create_time())
                cached = self._processes.get(key)
                observed = cached or process
                percent = observed.cpu_percent(interval=None)
                if cached is not None:
                    measured = _number(percent)
                    if measured is not None:
                        percentages.append(measured)
                else:
                    warming += 1
                rss += observed.memory_info().rss
                retained[key] = observed
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self._processes = retained
        app = float(sum(percentages)) if percentages else None
        capacity = effective_cpu_capacity()
        count = max(1, int(capacity))
        memory = psutil.virtual_memory()
        return {"system_percent": system, "process_percent": app,
                "app_system_percent": min(100., app / capacity) if app is not None else None,
                "process_percent_scope": "application_process_tree_one_logical_cpu=100%",
                "logical_cpus": count, "process_count": len(retained), "warming_processes": warming,
                "host_logical_cpus": os.cpu_count(), "normalization_cpu_capacity": capacity,
                "normalization_scope": "Minimum of host CPU count, process affinity and readable cgroup-v2 CPU quota.",
                "process_tree_rss_bytes": rss, "memory_total_bytes": memory.total, "memory_available_bytes": memory.available,
                "memory_scope": "Host/VM memory visible to psutil; process/cgroup memory limits are recorded separately in the execution plan. RSS is summed across processes and can double-count shared pages.",
                "reason": "Neu gestartete Prozesse benötigen zwei Messpunkte." if warming else None}

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                value = {"sampled_at": utcnow(), "interval_seconds": self.interval_seconds,
                         "cpu": self._cpu_sample(), "gpu": nvidia_sample(self._nvidia), "sampler_error": None}
                value["sampled_at"] = utcnow()
                with self._lock:
                    self._latest = value
                    self._last_sample = time.monotonic()
                if self.on_sample:
                    self.on_sample(copy.deepcopy(value))
            except Exception as exc:
                with self._lock:
                    self._latest["sampler_error"] = str(exc)[:500]
            self._stop.wait(max(0., self.interval_seconds - (time.perf_counter() - started)))

    def snapshot(self):
        with self._lock:
            value = copy.deepcopy(self._latest)
            age = time.monotonic() - self._last_sample if self._last_sample is not None else None
        value.update(age_seconds=age, stale=age is None or age > max(5., self.interval_seconds * 3), cached=True)
        return value

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.)
