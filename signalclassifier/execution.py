"""Bounded spawn workers and cooperative, crash-recoverable CPU timing gates.

Independent training uses processes, never shared Torch/NumPy RNG threads. A
POSIX shared file lock covers training; CPU benchmarks require the exclusive
lock. Pending benchmarks pause peers at their next progress checkpoint. Kernel
file locks are released even if a worker dies; parent-owned slot flags are then
cleared. External applications/receiver processing remain outside this gate.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import multiprocessing as mp
from multiprocessing.connection import wait
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path

try:
    import fcntl
except ImportError:  # Native Windows is not the supported USB/application path.
    fcntl = None

_CURRENT = {}
_GATE = None
_LOCAL_BENCHMARK = threading.RLock()
_SPAWN_LOCK = threading.Lock()
_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
_TRAINING, _WAITING_BENCHMARK, _BENCHMARK, _PAUSED = 1, 2, 3, 4


class ExecutionCancelled(Exception):
    """Cooperative worker cancellation; never an apparent completed model."""


class JobDeadlineExceeded(TimeoutError):
    pass


def normalize_execution(config: dict) -> dict:
    value = config.get("execution", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("execution muss ein Objekt sein")
    allowed = {"mode", "max_workers", "cpu_threads", "preprocessing_workers", "gpu_workers"}
    if set(value) - allowed:
        raise ValueError("Unbekannte execution-Felder: " + ", ".join(sorted(set(value) - allowed)))
    result = {"mode": "auto", "max_workers": 0, "cpu_threads": 0, "preprocessing_workers": 0, "gpu_workers": 0, **value}
    if result["mode"] not in ("auto", "serial", "parallel"):
        raise ValueError("execution.mode muss auto, serial oder parallel sein")
    for key, maximum in (("max_workers", 8), ("cpu_threads", 32), ("preprocessing_workers", 8), ("gpu_workers", 4)):
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= maximum:
            raise ValueError(f"execution.{key} muss eine Ganzzahl zwischen 0 (automatisch) und {maximum} sein")
    return result


def _cgroup_number(name):
    try:
        value = Path("/sys/fs/cgroup", name).read_text(encoding="ascii").strip()
        return int(value) if value.isdigit() else None
    except (OSError, ValueError):
        return None


def _cpu_details():
    host = max(1, os.cpu_count() or 1)
    affinity = host
    try:
        affinity = max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    quota = None
    try:
        limit, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if limit != "max" and int(period) > 0:
            quota = int(limit) / int(period)
    except (OSError, ValueError):
        pass
    capacity = min(float(host), float(affinity), quota if quota is not None else float(host))
    return {"host_logical_cpus": host, "affinity_cpus": affinity, "cgroup_cpu_quota": quota,
            "effective_cpu_capacity": max(.01, capacity)}


def effective_cpu_capacity() -> float:
    """CPU capacity for resource plans/CPU-% normalization; never imports Torch."""
    return _cpu_details()["effective_cpu_capacity"]


def probe_resources(include_gpu=True) -> dict:
    cpu = _cpu_details()
    result = {**cpu, "logical_cpus": max(1, int(cpu["effective_cpu_capacity"])), "physical_cpus": None,
              "available_ram_bytes": None, "ram_bytes": None,
              "cuda_available": False, "gpu_memory_bytes": None, "gpu_free_memory_bytes": None,
              "gpu_name": None, "gpu_probe_performed": False, "posix_file_lock_available": fcntl is not None}
    try:
        import psutil
        memory = psutil.virtual_memory()
        memory_limit, memory_current = _cgroup_number("memory.max"), _cgroup_number("memory.current")
        available = min(int(memory.available), max(0, memory_limit - (memory_current or 0))) if memory_limit else int(memory.available)
        physical = psutil.cpu_count(logical=False)
        result.update(physical_cpus=min(physical, result["logical_cpus"]) if physical else None,
                      host_physical_cpus=physical, host_ram_bytes=int(memory.total),
                      host_available_ram_bytes=int(memory.available), cgroup_memory_limit_bytes=memory_limit,
                      cgroup_memory_current_bytes=memory_current,
                      ram_bytes=min(int(memory.total), memory_limit) if memory_limit else int(memory.total),
                      available_ram_bytes=available,
                      resource_scope="minimum of host, CPU affinity and readable cgroup-v2 CPU/memory limits")
    except (ImportError, OSError):
        pass
    if include_gpu and importlib.util.find_spec("torch") is not None:
        try:
            import torch
            result["gpu_probe_performed"] = True
            if torch.cuda.is_available():
                properties = torch.cuda.get_device_properties(0)
                free, total = torch.cuda.mem_get_info(0)
                result.update(cuda_available=True, gpu_name=properties.name,
                              gpu_memory_bytes=int(total), gpu_free_memory_bytes=int(free))
        except (ImportError, OSError, RuntimeError):
            pass
    return result


def _positive(value, default):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else default


def plan_execution(config: dict, resources: dict | None = None, jobs=None) -> dict:
    requested = normalize_execution(config)
    needs_gpu = config.get("device", "auto") != "cpu" and (jobs is None or any(not _cpu_only(job) for job in jobs))
    hardware = dict(probe_resources(include_gpu=needs_gpu) if resources is None else resources)
    count = max(1, len(jobs) if jobs is not None else len(config.get("topologies", [])) or len(config.get("topology_ids", [])) or 1)
    gib = 1024 ** 3
    logical = int(_positive(hardware.get("logical_cpus"), 1))
    cpu_budget = max(1, logical - 1)
    available_value = hardware.get("available_ram_bytes")
    available_ram = int(available_value) if isinstance(available_value, (int, float)) and math.isfinite(available_value) and available_value >= 0 else 2 * gib
    ram_budget = max(0, available_ram - max(512 * 1024 ** 2, available_ram // 5))
    lengths = config.get("segment_lengths") or [config.get("segment_length", 2048)]
    search = config.get("optimization", {}).get("search_space", {}) if isinstance(config.get("optimization"), dict) else {}
    lengths = search.get("segment_lengths") or lengths
    length = max((int(_positive(value, 2048)) for value in lengths), default=2048)
    classes = max(2, len(config.get("classes", [])))
    raw = min(128 * 1024 ** 2, int(_positive(config.get("samples_per_class"), 80)) * classes * length * 8)
    ram_per_worker = gib + 6 * raw
    ram_workers = max(1, ram_budget // ram_per_worker)
    free_value = hardware.get("gpu_free_memory_bytes")
    gpu_free = int(free_value) if isinstance(free_value, (int, float)) and math.isfinite(free_value) and free_value >= 0 else int(_positive(hardware.get("gpu_memory_bytes"), 0))
    gpu_budget = max(0, gpu_free - max(512 * 1024 ** 2, gpu_free // 8))
    gpu_per_worker = 2 * gib + min(gib // 2, raw * 4)
    gpu_limit = min(4, gpu_budget // gpu_per_worker) if hardware.get("cuda_available") and config.get("device", "auto") != "cpu" else 0
    gpu_workers = min(requested["gpu_workers"] or 2, gpu_limit)
    auto_workers = min(4, max(1, cpu_budget // 2), max(1, gpu_workers + 2) if gpu_workers else 4)
    workers = min(requested["max_workers"] or auto_workers, 8, cpu_budget, ram_workers, count)
    warnings = []
    if requested["mode"] == "serial":
        workers = 1
    if not hardware.get("posix_file_lock_available", fcntl is not None):
        workers = 1
        warnings.append("Keine POSIX-Dateisperren: Ausführung für verlässliche Workerkoordination seriell begrenzt.")
    workers = max(1, int(workers))
    gpu_workers = min(int(gpu_workers), workers)
    cpu_threads = min(requested["cpu_threads"] or max(1, cpu_budget // workers), 32, max(1, cpu_budget // workers))
    preprocessing = min(requested["preprocessing_workers"] or 1, 8, cpu_threads)
    if requested["max_workers"] and workers < min(count, requested["max_workers"]):
        warnings.append("Workerzahl wurde durch CPU-/RAM-/Jobbudget oder seriellen Modus begrenzt.")
    if requested["cpu_threads"] and cpu_threads < requested["cpu_threads"]:
        warnings.append("CPU-Threads je Worker wurden zum Schutz des gemeinsamen CPU-Budgets begrenzt.")
    if requested["preprocessing_workers"] and preprocessing < requested["preprocessing_workers"]:
        warnings.append("Vorverarbeitungsworker wurden auf das CPU-Budget je Prozess begrenzt.")
    if requested["gpu_workers"] and gpu_workers < requested["gpu_workers"]:
        warnings.append("GPU-Worker wurden durch verfügbaren GPU-Speicher oder Gesamtworkerzahl begrenzt.")
    if available_ram < ram_per_worker:
        warnings.append("Geschätzter RAM-Bedarf eines Workers überschreitet die aktuell verfügbare Reserve; Speichergrenzen praktisch prüfen.")
    warnings.append("RAM-/GPU-Budgets sind konservative Zulassungsschätzungen, keine gemessenen Peak-Garantien.")
    if not requested["preprocessing_workers"]:
        warnings.append("Automatische Vorverarbeitung nutzt einen Worker; zusätzliche Threads brachten im dokumentierten Referenzversuch keinen stabilen Vorteil.")
    return {"requested": requested,
            "resolved": {"mode": "parallel" if workers > 1 else "serial", "max_workers": workers,
                         "cpu_threads": cpu_threads, "preprocessing_workers": preprocessing,
                         "gpu_workers": gpu_workers, "cpu_budget": cpu_budget,
                         "ram_budget_bytes": ram_budget, "estimated_ram_per_worker_bytes": ram_per_worker,
                         "gpu_memory_budget_bytes": gpu_budget, "estimated_gpu_per_worker_bytes": gpu_per_worker},
            "resource_snapshot": hardware, "warnings": warnings, "process_start_method": "spawn",
            "benchmark_policy": "experiment_workers_quiescent", "external_host_load_verified": False}


def get_current_execution() -> dict:
    return dict(_CURRENT)


def get_wait_seconds() -> float:
    return float(_CURRENT.get("coordination_wait_seconds", 0.))


def get_wait_breakdown() -> dict:
    return dict(_CURRENT.get("coordination_wait", {"training_pause_seconds": 0., "cpu_benchmark_wait_seconds": 0.}))


def _add_wait(kind, seconds):
    buckets = _CURRENT.setdefault("coordination_wait", {"training_pause_seconds": 0., "cpu_benchmark_wait_seconds": 0.})
    buckets[kind] += max(0., seconds)
    _CURRENT["coordination_wait_seconds"] = sum(buckets.values())


def _check_cancel():
    if _GATE is None:
        return
    if _GATE["deadline"] is not None and time.monotonic() >= _GATE["deadline"]:
        raise JobDeadlineExceeded("Zeitbudget des Ausführungsjobs erreicht")
    if _GATE["cancel"].is_set():
        raise ExecutionCancelled("Experimentausführung abgebrochen")


def _sync_device():
    if _CURRENT.get("device") == "cuda":
        import sys
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_initialized():
            torch.cuda.synchronize()


def _gate_notice(phase):
    _GATE["queue"].put({"kind": "phase", "index": _GATE["index"], "phase": phase})


def _training_enter():
    if _GATE is None:
        return
    slot, states, handle = _GATE["slot"], _GATE["states"], _GATE["file"]
    states[slot] = _PAUSED
    _gate_notice("waiting_for_training_slot")
    waited, started = False, time.monotonic()
    try:
        while True:
            _check_cancel()
            if any(value in (_WAITING_BENCHMARK, _BENCHMARK) for value in states):
                waited = True
                time.sleep(.02)
                continue
            try:
                if fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                # A pending writer might have appeared while acquiring the lock.
                if any(value in (_WAITING_BENCHMARK, _BENCHMARK) for value in states):
                    if fcntl is not None:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    waited = True
                    time.sleep(.02)
                    continue
                states[slot] = _TRAINING
                _gate_notice("compute")
                return
            except BlockingIOError:
                waited = True
                time.sleep(.02)
    finally:
        if waited:
            _add_wait("training_pause_seconds", time.monotonic() - started)


def cooperative_checkpoint():
    if _GATE is None:
        return
    _check_cancel()
    slot, states = _GATE["slot"], _GATE["states"]
    if states[slot] == _TRAINING and any(value in (_WAITING_BENCHMARK, _BENCHMARK) for value in states):
        _sync_device()
        states[slot] = _PAUSED
        if fcntl is not None:
            fcntl.flock(_GATE["file"], fcntl.LOCK_UN)
        _gate_notice("paused_for_cpu_benchmark")
        _training_enter()


@contextlib.contextmanager
def cpu_benchmark_phase():
    """Exclude all registered experiment compute, not external host workloads."""
    if _GATE is None:
        with _LOCAL_BENCHMARK:
            yield
        return
    _check_cancel()
    slot, states, handle = _GATE["slot"], _GATE["states"], _GATE["file"]
    if states[slot] == _BENCHMARK:
        yield  # Nested wrappers in hardware/reference helpers are reentrant.
        return
    _sync_device()
    states[slot] = _WAITING_BENCHMARK
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
    _gate_notice("waiting_for_cpu_benchmark")
    waited, started = False, time.monotonic()
    charged = False
    try:
        while True:
            _check_cancel()
            try:
                if fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                states[slot] = _BENCHMARK
                if waited:
                    _add_wait("cpu_benchmark_wait_seconds", time.monotonic() - started)
                    charged = True
                _gate_notice("cpu_benchmark")
                break
            except BlockingIOError:
                waited = True
                time.sleep(.02)
        yield
    finally:
        if waited and not charged:
            _add_wait("cpu_benchmark_wait_seconds", time.monotonic() - started)
        states[slot] = _PAUSED
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        _training_enter()


def _run_ml(config, output, progress, **kwargs):
    from . import ml
    return ml.run_experiment(config, output, progress, **kwargs)


class _PipeSender:
    def __init__(self, connection):
        self.connection = connection

    def put(self, value):
        self.connection.send(value)


def _worker(index, slot, job, context, gate_path, states, cancel, channel, runner):
    global _CURRENT, _GATE
    _CURRENT = dict(context, pid=os.getpid(), coordination_wait_seconds=0.,
                    coordination_wait={"training_pause_seconds": 0., "cpu_benchmark_wait_seconds": 0.})
    channel = _PipeSender(channel)
    for name in _THREAD_ENV:
        os.environ[name] = str(context["resolved_cpu_threads"])
    started = time.monotonic()
    output = Path(job["output_dir"])
    handle = None
    status, error, path = "failed", None, None
    try:
        output.mkdir(parents=True, exist_ok=True)
        handle = open(gate_path, "a+b")
        _GATE = {"slot": slot, "states": states, "file": handle, "cancel": cancel, "queue": channel,
                 "index": index, "deadline": job.get("deadline")}
        _training_enter()

        def progress(value):
            cooperative_checkpoint()
            channel.put({"kind": "progress", "index": index, "update": value})

        config = {**job["config"], "device": context["device"]}
        kwargs = {**job.get("kwargs", {}), "_execution_context": {**job.get("kwargs", {}).get("_execution_context", {}), **_CURRENT}}
        report = runner(config, output, progress, **kwargs)
        _check_cancel()
        if not isinstance(report, dict):
            raise ValueError("Worker lieferte keinen vollständigen Modellbericht")
        report["execution_worker"] = dict(_CURRENT, benchmark_policy="experiment_workers_quiescent", external_host_load_verified=False)
        path = output / "execution-result.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
        status = "completed"
    except JobDeadlineExceeded as exc:
        status, error = "timed_out", str(exc)
    except ExecutionCancelled as exc:
        status, error = "cancelled", str(exc)
    except BaseException as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        channel.put({"kind": "traceback", "index": index, "traceback": traceback.format_exc()})
    finally:
        if runner == _run_ml:
            import sys
            module = sys.modules.get("signalclassifier.ml")
            cleanup = getattr(module, "release_execution_job", None)
            if cleanup is not None:
                cleanup()
        if handle is not None:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        states[slot] = 0
        try:
            channel.put({"kind": "result", "index": index, "status": status, "error": error,
                         "report_path": str(path) if status == "completed" else None,
                         "elapsed_seconds": time.monotonic() - started, "pid": os.getpid()})
        except (BrokenPipeError, EOFError, OSError):
            pass
        _GATE, _CURRENT = None, {}


def _slot_worker(slot, gate_path, states, cancel, connection, runner, base_context, preload_torch):
    """Keep costly NumPy/SciPy/Torch imports alive across independent jobs."""
    global _CURRENT, _GATE
    started = time.monotonic()
    for name in _THREAD_ENV:
        os.environ[name] = str(base_context["resolved_cpu_threads"])
    handle = None
    try:
        _CURRENT = dict(base_context, pid=os.getpid(), job_id="__worker_startup__")
        handle = open(gate_path, "a+b")
        _GATE = {"slot": slot, "states": states, "file": handle, "cancel": cancel,
                 "queue": _PipeSender(connection), "index": -1, "deadline": None}
        _training_enter()
        if runner == _run_ml:
            from . import ml
            if preload_torch:
                try:
                    import torch
                    torch.set_num_threads(base_context["resolved_cpu_threads"])
                    try:
                        torch.set_num_interop_threads(1)
                    except RuntimeError:
                        pass  # A custom __main__ may already have initialized it.
                    base_context["torch_interop_threads"] = torch.get_num_interop_threads()
                except ImportError:
                    if preload_torch:
                        raise
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        handle = None
        states[slot] = 0
        _GATE, _CURRENT = None, {}
        startup_seconds = time.monotonic() - started
        connection.send({"kind": "ready", "startup_seconds": startup_seconds, "pid": os.getpid()})
        while True:
            command = connection.recv()
            if command.get("command") == "stop":
                break
            if command.get("command") != "run":
                raise ValueError("Ungültiger Worker-Steuerbefehl")
            context = {**base_context, **command["context"], "dependency_startup_seconds": startup_seconds}
            _worker(command["index"], slot, command["job"], context, gate_path, states, cancel, connection, runner)
    except (EOFError, BrokenPipeError, ExecutionCancelled):
        pass
    except BaseException:
        try:
            connection.send({"kind": "startup_error", "error": traceback.format_exc()})
        except (OSError, BrokenPipeError):
            pass
    finally:
        if handle is not None:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        states[slot] = 0
        _GATE, _CURRENT = None, {}
        connection.close()


def _cpu_only(job):
    config = job["config"]
    return config.get("topology_id") == "feature-lda" or config.get("topology", {}).get("family") == "feature_lda"


def _nested_jobs(jobs, config, progress, axis, runner):
    """One parallel axis: already isolated topology workers keep HPO serial."""
    started = time.monotonic()
    outcomes = []
    for index, job in enumerate(jobs):
        cooperative_checkpoint()
        begin = time.monotonic()
        deadline = job.get("deadline")

        def child_progress(update):
            if deadline is not None and time.monotonic() >= deadline:
                raise JobDeadlineExceeded("Zeitbudget des Suchversuchs erreicht")
            cooperative_checkpoint()
            if progress:
                progress({**update, "job_index": index, "topology_id": job.get("topology_id"), "trial": job.get("trial")})

        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise JobDeadlineExceeded("Zeitbudget vor Start erreicht")
            kwargs = {**job.get("kwargs", {}), "_execution_context": {**job.get("kwargs", {}).get("_execution_context", {}), **_CURRENT}}
            report = runner({**job["config"], "device": _CURRENT["device"]}, Path(job["output_dir"]), child_progress, **kwargs)
            output = Path(job["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            report_path = output / "execution-result.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            outcomes.append({"id": job["id"], "index": index, "status": "completed", "report": report,
                             "report_path": str(report_path), "elapsed_seconds": time.monotonic() - begin, "pid": os.getpid(), "device": _CURRENT["device"]})
        except JobDeadlineExceeded as exc:
            outcomes.append({"id": job["id"], "index": index, "status": "timed_out", "error": str(exc),
                             "elapsed_seconds": time.monotonic() - begin, "pid": os.getpid(), "device": _CURRENT["device"]})
        except ExecutionCancelled:
            raise
        except Exception as exc:
            outcomes.append({"id": job["id"], "index": index, "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                             "elapsed_seconds": time.monotonic() - begin, "pid": os.getpid(), "device": _CURRENT["device"]})
        if progress:
            progress({"stage": "execution_job_complete", "execution_job_result": {key: value for key, value in outcomes[-1].items() if key != "report"}})
    return {"outcomes": outcomes, "execution": {"axis": axis, "nested_parallelism_disabled": True,
            "resolved": {"mode": "serial", "max_workers": 1, "cpu_threads": _CURRENT["resolved_cpu_threads"],
                         "preprocessing_workers": _CURRENT["resolved_preprocessing_workers"], "gpu_workers": int(_CURRENT["device"] == "cuda")},
            "process_start_method": "inherited_spawn", "wall_seconds": time.monotonic() - started,
            "benchmark_policy": "experiment_workers_quiescent", "external_host_load_verified": False}}


def execute_jobs(jobs, config, progress=None, *, axis="topologies", fail_fast=False, _runner=None, _resources=None):
    """Run ordered jobs in bounded spawn processes; preserve partial failures.

    Jobs: {id, config, output_dir, kwargs?, topology_id?, trial?, deadline?}.
    `deadline` is absolute time.monotonic(), including cooperative grace. Reports
    travel through per-job JSON files; small progress/control messages use a
    dedicated pipe per worker, avoiding shared Queue corruption on termination.
    Private runner/resource injection is for deterministic software tests only.
    """
    call_started = time.monotonic()
    if not isinstance(jobs, list) or len(jobs) > 1000:
        raise ValueError("Ausführungsjobs müssen eine Liste mit höchstens 1000 Einträgen sein")
    normalized = []
    identifiers, directories = set(), set()
    for index, value in enumerate(jobs):
        if not isinstance(value, dict) or not isinstance(value.get("config"), dict) or not value.get("output_dir"):
            raise ValueError("Jeder Job benötigt config und einen eigenen output_dir")
        identifier = str(value.get("id", index))
        directory = Path(value["output_dir"]).resolve()
        if identifier in identifiers or directory in directories:
            raise ValueError("Job-IDs und Ausgabeordner müssen innerhalb einer Ausführung eindeutig sein")
        if value.get("deadline") is not None and (isinstance(value["deadline"], bool) or not isinstance(value["deadline"], (int, float)) or not math.isfinite(value["deadline"])):
            raise ValueError("Jobdeadline muss eine endliche monotonic-Zeit sein")
        identifiers.add(identifier)
        directories.add(directory)
        normalized.append({**value, "id": identifier, "output_dir": str(directory)})
    jobs, runner = normalized, _runner or _run_ml
    if _CURRENT.get("nested_parallelism_disabled"):
        return _nested_jobs(jobs, config, progress, axis, runner)
    probe_started = time.monotonic()
    plan = plan_execution(config, resources=_resources, jobs=jobs)
    plan["resource_probe_seconds"] = time.monotonic() - probe_started
    plan.update(axis=axis, peak_active_workers=0, jobs=[], warnings=list(plan["warnings"]))
    if not jobs:
        return {"outcomes": [], "execution": dict(plan, wall_seconds=0., completed_jobs=0, failed_jobs=0, timed_out_jobs=0, cancelled_jobs=0)}
    if plan["resolved"]["ram_budget_bytes"] < plan["resolved"]["estimated_ram_per_worker_bytes"]:
        raise ValueError("Verfügbare RAM-Reserve reicht nicht für einen geschätzten Ausführungsworker; laufende Last reduzieren")
    cuda_requested = any(not _cpu_only(job) and job["config"].get("device", config.get("device", "auto")) == "cuda" for job in jobs)
    if cuda_requested and not plan["resource_snapshot"].get("cuda_available"):
        raise ValueError("CUDA wurde gewählt, ist aber für diese Ausführung nicht verfügbar")
    if cuda_requested and plan["resolved"]["gpu_workers"] == 0:
        raise ValueError("CUDA-Speicherreserve reicht für keinen geschätzten GPU-Worker; CPU wählen oder GPU-Speicher freigeben")
    context = mp.get_context("spawn")
    states = context.Array("i", 8, lock=False)
    capacity = plan["resolved"]["max_workers"]
    outcomes = [None] * len(jobs)
    fractions = [0.] * len(jobs)
    pending = list(range(len(jobs)))
    active, pool = {}, {}
    started, last_heartbeat = call_started, 0.
    stop_scheduling = False
    plan.update(worker_reuse=True, worker_startups=[], spawned_processes=0,
                startup_scope="Spawn and explicit dependency preload; device-context initialization can remain in job elapsed time.")

    def status():
        return {"active_workers": len(active), "queued_jobs": len(pending),
                "completed_jobs": sum(result is not None for result in outcomes), "total_jobs": len(jobs),
                "workers": [{"job_index": index, "topology_id": jobs[index].get("topology_id") or jobs[index]["config"].get("topology_id"),
                             "trial": jobs[index].get("trial"), "phase": worker["phase"], "status": "running",
                             "progress": fractions[index], "pid": worker["process"].pid, "device": worker["job_device"]}
                            for index, worker in active.items()]}

    def emit(update=None):
        if progress:
            progress({"stage": "execution", "message": "Ausführungsworker koordinieren", **(update or {}),
                      "progress": sum(fractions) / len(jobs), "execution_progress": status()})

    def finish(worker, message):
        nonlocal stop_scheduling
        index = worker["index"]
        if index is None or outcomes[index] is not None:
            return
        result = {"id": jobs[index]["id"], "index": index, "status": message["status"],
                  "error": message.get("error"), "elapsed_seconds": message.get("elapsed_seconds", time.monotonic() - worker["job_started"]),
                  "pid": worker["process"].pid, "device": worker["job_device"],
                  "startup_seconds": worker.get("startup_seconds"), "report_path": message.get("report_path")}
        if worker.get("traceback"):
            result["traceback"] = worker["traceback"]
        if result["status"] == "completed":
            path = Path(message["report_path"])
            if path != Path(jobs[index]["output_dir"]) / "execution-result.json" or path.is_symlink():
                raise ValueError("Ungültiger Worker-Berichtspfad")
            try:
                result["report"] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                result.update(status="failed", error=f"Workerbericht nicht lesbar: {exc}")
        outcomes[index] = result
        fractions[index] = 1.
        active.pop(index, None)
        worker.update(index=None, phase="idle", deadline_triggered=None, cancel_triggered=None)
        plan["jobs"].append({key: value for key, value in result.items() if key != "report"})
        if fail_fast and result["status"] != "completed":
            stop_scheduling = True
        emit({"stage": "execution_job_complete", "job_index": index,
              "topology_id": jobs[index].get("topology_id") or jobs[index]["config"].get("topology_id"),
              "trial": jobs[index].get("trial"),
              "execution_job_result": {key: value for key, value in result.items() if key not in ("report", "traceback")}})

    def requested_device(index):
        return jobs[index]["config"].get("device", config.get("device", "auto"))

    def compatible(worker, index):
        if _cpu_only(jobs[index]) or requested_device(index) == "cpu":
            return "cpu"
        if worker["resource_device"] == "cuda":
            return "cuda"
        return "cpu" if requested_device(index) != "cuda" else None

    def assign(worker, index, device):
        pending.remove(index)
        worker.update(index=index, job_device=device, phase="starting" if not worker["ready"] else "compute",
                      job_started=time.monotonic(), deadline_triggered=None, cancel_triggered=None, traceback=None)
        active[index] = worker
        if worker["ready"]:
            dispatch(worker)
        plan["peak_active_workers"] = max(plan["peak_active_workers"], len(active))
        emit()

    def dispatch(worker):
        index = worker["index"]
        worker["cancel"].clear()
        worker["job_started"] = time.monotonic()
        worker_context = {"job_id": jobs[index]["id"], "device": worker["job_device"],
                          "startup_seconds": worker.get("startup_seconds", 0.)}
        worker["pipe"].send({"command": "run", "index": index, "job": jobs[index], "context": worker_context})

    def shutdown_workers():
        for worker in pool.values():
            if worker["index"] is not None:
                worker["cancel"].set()
            elif worker["ready"] and worker["process"].is_alive():
                try:
                    worker["pipe"].send({"command": "stop"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
        deadline = time.monotonic() + 2
        while pool and time.monotonic() < deadline:
            for worker in list(pool.values()):
                try:
                    if worker["pipe"].poll(.01):
                        worker["pipe"].recv()
                except (EOFError, OSError):
                    pass
            if all(not worker["process"].is_alive() for worker in pool.values()):
                break
        for worker in pool.values():
            process = worker["process"]
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            states[worker["slot"]] = 0
            worker["pipe"].close()

    with tempfile.TemporaryDirectory(prefix="signalclassifier-execution-") as temporary:
        gate_path = str(Path(temporary) / "cpu-timing.lock")
        Path(gate_path).touch()
        try:
            while pending or active:
                now = time.monotonic()
                for index in list(pending):
                    if jobs[index].get("deadline") is not None and now >= jobs[index]["deadline"]:
                        pending.remove(index)
                        outcomes[index] = {"id": jobs[index]["id"], "index": index, "status": "timed_out",
                                           "error": "Zeitbudget vor Jobstart erreicht", "elapsed_seconds": 0., "pid": None, "device": None}
                        fractions[index] = 1.
                        emit({"stage": "execution_job_complete", "execution_job_result": outcomes[index].copy()})
                if not stop_scheduling:
                    # Prefer GPU slots for eligible jobs, leaving CPU slots free.
                    idle = sorted((worker for worker in pool.values() if worker["index"] is None and worker["ready"]),
                                  key=lambda worker: worker["resource_device"] != "cuda")
                    for worker in idle:
                        if (worker["resource_device"] == "cpu"
                                and sum(other["resource_device"] == "cuda" for other in pool.values()) < plan["resolved"]["gpu_workers"]
                                and any(not _cpu_only(jobs[i]) and requested_device(i) != "cpu" for i in pending)):
                            worker["resource_device"] = "cuda"  # A CPU-only slot has no retained CUDA context yet.
                        index = next((i for i in pending if compatible(worker, i) is not None
                                      and (worker["resource_device"] != "cuda" or not _cpu_only(jobs[i])
                                           or not any(other["resource_device"] == "cpu" and other["index"] is None and other["ready"] for other in pool.values()))), None)
                        if index is not None:
                            assign(worker, index, compatible(worker, index))
                    while pending and len(pool) < capacity:
                        gpu_reserved = sum(worker["resource_device"] == "cuda" for worker in pool.values())
                        selected, resource_device = None, None
                        for index in pending:
                            if _cpu_only(jobs[index]) or requested_device(index) == "cpu":
                                selected, resource_device = index, "cpu"
                                break
                            if gpu_reserved < plan["resolved"]["gpu_workers"]:
                                selected, resource_device = index, "cuda"
                                break
                            if requested_device(index) != "cuda":
                                selected, resource_device = index, "cpu"
                                break
                        if selected is None:
                            break
                        slot = next(value for value in range(8) if value not in pool)
                        parent, child = context.Pipe(duplex=True)
                        cancel = context.Event()
                        base = {"resolved_cpu_threads": plan["resolved"]["cpu_threads"],
                                "resolved_preprocessing_workers": plan["resolved"]["preprocessing_workers"],
                                "process_isolated": True, "nested_parallelism_disabled": True,
                                "axis": axis, "device": resource_device, "worker_index": slot,
                                "resource_snapshot": plan["resource_snapshot"],
                                "benchmark_lock_path": gate_path}
                        preload_torch = any(not _cpu_only(job) for job in jobs) and not (resource_device == "cpu" and config.get("device") == "cuda")
                        process = context.Process(target=_slot_worker, args=(slot, gate_path, states, cancel, child, runner, base, preload_torch),
                                                  name=f"sdr-{axis}-slot-{slot + 1}")
                        try:
                            # Spawn imports __main__ before entering our target.
                            # Child NumPy/BLAS imports must already see its budget.
                            with _SPAWN_LOCK:
                                previous = {name: os.environ.get(name) for name in _THREAD_ENV}
                                try:
                                    for name in _THREAD_ENV:
                                        os.environ[name] = str(plan["resolved"]["cpu_threads"])
                                    process.start()
                                finally:
                                    for name, value in previous.items():
                                        if value is None:
                                            os.environ.pop(name, None)
                                        else:
                                            os.environ[name] = value
                        except BaseException:
                            parent.close(); child.close()
                            raise
                        child.close()
                        worker = {"process": process, "pipe": parent, "cancel": cancel, "slot": slot,
                                  "resource_device": resource_device, "ready": False, "index": None,
                                  "phase": "initializing", "created": time.monotonic(), "startup_seconds": None}
                        pool[slot] = worker
                        plan["spawned_processes"] += 1
                        assign(worker, selected, resource_device if not _cpu_only(jobs[selected]) else "cpu")
                ready = wait([worker["pipe"] for worker in pool.values()], timeout=.05) if pool else []
                for pipe in ready:
                    worker = next(worker for worker in pool.values() if worker["pipe"] is pipe)
                    try:
                        message = pipe.recv()
                    except (EOFError, OSError):
                        message = None
                    if message:
                        kind, index = message["kind"], worker["index"]
                        if kind == "ready":
                            worker.update(ready=True, startup_seconds=time.monotonic() - worker["created"])
                            plan["worker_startups"].append({"pid": worker["process"].pid, "device": worker["resource_device"],
                                                            "startup_seconds": worker["startup_seconds"],
                                                            "dependency_startup_seconds": message["startup_seconds"]})
                            if index is not None:
                                dispatch(worker)
                        elif kind == "progress" and index is not None:
                            update = message.get("update") if isinstance(message.get("update"), dict) else {}
                            fraction = update.get("progress", fractions[index])
                            if isinstance(fraction, (int, float)) and math.isfinite(fraction):
                                fractions[index] = max(fractions[index], min(1., max(0., fraction)))
                            worker["phase"] = update.get("stage", "compute")
                            emit({**update, "job_index": index, "topology_id": jobs[index].get("topology_id") or jobs[index]["config"].get("topology_id"),
                                  "trial": jobs[index].get("trial")})
                        elif kind == "phase":
                            worker["phase"] = message["phase"]
                        elif kind == "result":
                            finish(worker, message)
                        elif kind in ("traceback", "startup_error"):
                            worker["traceback"] = message.get("traceback") or message.get("error")
                            if kind == "startup_error":
                                worker["failure_error"] = str(message.get("error", "Workerinitialisierung fehlgeschlagen")).strip().splitlines()[-1]
                for slot, worker in list(pool.items()):
                    process, index = worker["process"], worker["index"]
                    deadline = jobs[index].get("deadline") if index is not None else None
                    if deadline is not None and time.monotonic() >= deadline:
                        worker["cancel"].set()
                        worker["deadline_triggered"] = worker["deadline_triggered"] or time.monotonic()
                    trigger = worker.get("deadline_triggered") or worker.get("cancel_triggered")
                    if trigger and time.monotonic() - trigger > 2 and process.is_alive():
                        process.terminate()
                    if trigger and time.monotonic() - trigger > 4 and process.is_alive():
                        process.kill()
                    if not process.is_alive():
                        process.join(timeout=.1)
                        while worker["index"] is not None and worker["pipe"].poll():
                            try:
                                message = worker["pipe"].recv()
                            except (EOFError, OSError):
                                break
                            if message.get("kind") == "result":
                                finish(worker, message)
                        if worker["index"] is not None:
                            timed_out = worker.get("deadline_triggered") is not None
                            cancelled = worker.get("cancel_triggered") is not None
                            finish(worker, {"status": "timed_out" if timed_out else "cancelled" if cancelled else "failed",
                                            "error": "Job nach Zeitbudget beendet" if timed_out else "Job abgebrochen" if cancelled else
                                                     worker.get("failure_error") or f"Worker unerwartet beendet (Exitcode {process.exitcode})"})
                        states[slot] = 0
                        worker["pipe"].close()
                        del pool[slot]
                if stop_scheduling:
                    for index in pending:
                        outcomes[index] = {"id": jobs[index]["id"], "index": index, "status": "cancelled",
                                           "error": "Nach Fehler eines anderen Jobs nicht gestartet", "elapsed_seconds": 0., "pid": None, "device": None}
                        fractions[index] = 1.
                    pending.clear()
                    for worker in active.values():
                        worker["cancel"].set()
                        worker["cancel_triggered"] = worker["cancel_triggered"] or time.monotonic()
                if time.monotonic() - last_heartbeat >= .2:
                    emit()
                    last_heartbeat = time.monotonic()
        finally:
            shutdown_workers()
    plan.update(wall_seconds=time.monotonic() - started,
                startup_seconds_total=sum(worker["startup_seconds"] for worker in plan["worker_startups"]),
                **{name + "_jobs": sum(value is not None and value["status"] == name for value in outcomes)
                   for name in ("completed", "failed", "timed_out", "cancelled")})
    return {"outcomes": outcomes, "execution": plan}
