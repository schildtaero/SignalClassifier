"""Real spawn/control tests; resource/GPU plans are explicitly simulated."""
from __future__ import annotations

import multiprocessing
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from signalclassifier.execution import (cpu_benchmark_phase, execute_jobs,
    effective_cpu_capacity, get_current_execution, get_wait_seconds, get_wait_breakdown,
    normalize_execution, plan_execution, probe_resources)

_IMPORT_TIME_OPENBLAS_THREADS = os.environ.get("OPENBLAS_NUM_THREADS")


def numerical_control_runner(config, output, progress, **kwargs):
    """No fake Torch/GPU training: exercise process RNG and gate semantics."""
    random.seed(config.get("seed", 71))
    first = random.random()
    context = get_current_execution()
    if config.get("nested"):
        nested = execute_jobs([{"id": "inner", "config": {"device": "cpu"}, "output_dir": output / "inner"}],
                              {"execution": {"mode": "parallel", "max_workers": 8}},
                              progress=progress, axis="trials", _runner=numerical_control_runner)
        return {"outer_pid": os.getpid(), "nested": nested}
    if config.get("crash"):
        os._exit(17)
    if config.get("hang"):
        time.sleep(30)
    if config.get("fail"):
        raise ValueError("deliberate control-test error")
    intervals = []
    for step in range(3):
        start = time.monotonic()
        time.sleep(.025)
        intervals.append(["compute", start, time.monotonic()])
        progress({"stage": "train", "progress": .1 + .2 * step, "epoch": step + 1})
        if step == config.get("benchmark_step", 0):
            with cpu_benchmark_phase():
                if config.get("crash_in_benchmark"):
                    os._exit(19)
                start = time.monotonic()
                with cpu_benchmark_phase():
                    time.sleep(.04)
                intervals.append(["benchmark", start, time.monotonic()])
    progress({"stage": "complete", "progress": 1})
    return {"seeded": [first, random.random()], "intervals": intervals,
            "context": context, "kwargs_context": kwargs.get("_execution_context"),
            "omp_threads": os.environ["OMP_NUM_THREADS"], "import_time_openblas_threads": _IMPORT_TIME_OPENBLAS_THREADS,
            "coordination_wait_seconds": get_wait_seconds(), "coordination_wait": get_wait_breakdown(),
            "test_kwarg": kwargs.get("_evaluate_test")}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resources = {"logical_cpus": 8, "physical_cpus": 4,
                          "available_ram_bytes": 12 * 1024 ** 3, "cuda_available": False,
                          "posix_file_lock_available": True}

    def jobs(self, configs):
        return [{"id": f"job-{index}", "config": {"device": "cpu", "topology_id": "feature-lda", **value},
                 "topology_id": "feature-lda", "trial": index + 1, "output_dir": self.root / f"job-{index}",
                 "kwargs": {"_evaluate_test": False}}
                for index, value in enumerate(configs)]

    def run_jobs(self, jobs, **kwargs):
        return execute_jobs(jobs, {"device": "cpu", "execution": {"mode": "parallel", "max_workers": 2, "cpu_threads": 2}},
                            _runner=numerical_control_runner, _resources=self.resources, **kwargs)

    def test_normalize_and_conservative_gpu_cpu_ram_budget(self):
        self.assertEqual(normalize_execution({})["mode"], "auto")
        for execution in ({"mode": "threads"}, {"max_workers": 9}, {"gpu_workers": -1}, {"cpu_threads": True}, {"future": 1}):
            with self.assertRaises(ValueError):
                normalize_execution({"execution": execution})
        resources = {**self.resources, "logical_cpus": 12, "gpu_memory_bytes": 8 * 1024 ** 3,
                     "gpu_free_memory_bytes": 8 * 1024 ** 3, "cuda_available": True}
        plan = plan_execution({"device": "auto", "topology_ids": list(range(10))}, resources)
        self.assertEqual(plan["resolved"]["gpu_workers"], 2)
        self.assertEqual(plan["resolved"]["preprocessing_workers"], 1)
        self.assertLessEqual(plan["resolved"]["max_workers"] * plan["resolved"]["cpu_threads"], 11)
        self.assertLessEqual(plan["resolved"]["preprocessing_workers"], plan["resolved"]["cpu_threads"])
        low_memory = plan_execution({"execution": {"mode": "parallel", "max_workers": 8, "cpu_threads": 32}, "topology_ids": list(range(8))},
                                    {**self.resources, "available_ram_bytes": 2 * 1024 ** 3})
        self.assertEqual(low_memory["resolved"]["max_workers"], 1)
        self.assertTrue(low_memory["warnings"])

    def test_readable_cgroup_limits_bound_host_resources_without_torch(self):
        gib = 1024 ** 3

        def limits(path, *args, **kwargs):
            return {"cpu.max": "200000 100000", "memory.max": str(8 * gib), "memory.current": str(3 * gib)}[path.name]

        with mock.patch("signalclassifier.execution.os.cpu_count", return_value=192), \
             mock.patch("signalclassifier.execution.os.sched_getaffinity", return_value={1, 2, 3}), \
             mock.patch.object(Path, "read_text", limits), \
             mock.patch("signalclassifier.execution.importlib.util.find_spec", return_value=None), \
             mock.patch("psutil.virtual_memory", return_value=SimpleNamespace(total=256 * gib, available=220 * gib)), \
             mock.patch("psutil.cpu_count", return_value=96):
            self.assertEqual(effective_cpu_capacity(), 2)
            resources = probe_resources()
        self.assertEqual(resources["logical_cpus"], 2)
        self.assertEqual(resources["ram_bytes"], 8 * gib)
        self.assertEqual(resources["available_ram_bytes"], 5 * gib)
        self.assertEqual(resources["host_logical_cpus"], 192)
        self.assertEqual(resources["affinity_cpus"], 3)
        self.assertEqual(resources["cgroup_cpu_quota"], 2)

    def test_persistent_workers_reuse_pids_and_emit_incremental_completion(self):
        completions = []

        def record(update):
            if "execution_job_result" in update:
                value = update["execution_job_result"]
                self.assertNotIn("report", value)
                self.assertTrue(Path(value["report_path"]).is_file())
                completions.append(value)

        result = self.run_jobs(self.jobs([{} for _ in range(6)]), progress=record)
        outcomes = result["outcomes"]
        self.assertTrue(all(outcome["status"] == "completed" for outcome in outcomes))
        self.assertEqual(len({outcome["pid"] for outcome in outcomes}), 2)
        self.assertEqual(result["execution"]["spawned_processes"], 2)
        self.assertEqual(len(completions), 6)
        self.assertEqual(len(result["execution"]["worker_startups"]), 2)
        self.assertGreater(result["execution"]["startup_seconds_total"], 0)
        self.assertTrue(all(outcome["report"]["seeded"] == outcomes[0]["report"]["seeded"] for outcome in outcomes))

    def test_nested_trial_axis_stays_inside_existing_isolated_process(self):
        result = self.run_jobs(self.jobs([{"nested": True}]))
        outer = result["outcomes"][0]
        self.assertEqual(outer["status"], "completed", outer.get("error"))
        nested = outer["report"]["nested"]
        self.assertEqual(nested["outcomes"][0]["pid"], outer["pid"])
        self.assertEqual(nested["execution"]["resolved"]["max_workers"], 1)
        self.assertEqual(nested["execution"]["process_start_method"], "inherited_spawn")

    def test_real_spawn_isolates_rng_preserves_order_and_quiesces_peer_compute(self):
        updates = []
        result = self.run_jobs(self.jobs([{"benchmark_step": 0}, {"benchmark_step": 1}]), progress=updates.append)
        outcomes = result["outcomes"]
        self.assertEqual([row["id"] for row in outcomes], ["job-0", "job-1"])
        self.assertEqual([row["status"] for row in outcomes], ["completed", "completed"])
        self.assertNotEqual(outcomes[0]["pid"], outcomes[1]["pid"])
        self.assertEqual(outcomes[0]["report"]["seeded"], outcomes[1]["report"]["seeded"])
        self.assertTrue(any(update["execution_progress"]["active_workers"] == 2 for update in updates))
        for index, outcome in enumerate(outcomes):
            report = outcome["report"]
            self.assertEqual(report["context"]["resolved_cpu_threads"], 2)
            self.assertEqual(report["omp_threads"], "2")
            self.assertFalse(report["test_kwarg"])
            self.assertTrue(report["context"]["nested_parallelism_disabled"])
            other = outcomes[1 - index]["report"]["intervals"]
            for kind, start, end in report["intervals"]:
                if kind == "benchmark":
                    self.assertFalse(any(max(start, peer_start) < min(end, peer_end)
                                         for _, peer_start, peer_end in other), "CPU benchmark overlapped another registered worker")
        self.assertEqual(result["execution"]["peak_active_workers"], 2)
        self.assertFalse(result["execution"]["external_host_load_verified"])
        self.assertGreater(sum(outcome["report"]["coordination_wait_seconds"] for outcome in outcomes), 0)
        for outcome in outcomes:
            report = outcome["report"]
            self.assertAlmostEqual(report["coordination_wait_seconds"], sum(report["coordination_wait"].values()))

    def test_thread_environment_is_inherited_before_child_main_import_and_restored(self):
        with mock.patch.dict(os.environ, {"OPENBLAS_NUM_THREADS": "31", "OMP_NUM_THREADS": "31"}):
            result = self.run_jobs(self.jobs([{}]))
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "31")
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "31")
        report = result["outcomes"][0]["report"]
        self.assertEqual(report["import_time_openblas_threads"], "2")
        self.assertEqual(report["omp_threads"], "2")

    def test_worker_crash_releases_gate_and_other_jobs_continue(self):
        result = self.run_jobs(self.jobs([{"crash": True}, {}]))
        self.assertEqual(result["outcomes"][0]["status"], "failed")
        self.assertIn("17", result["outcomes"][0]["error"])
        self.assertEqual(result["outcomes"][1]["status"], "completed")
        self.assertEqual(result["execution"]["failed_jobs"], 1)

    def test_worker_crash_inside_exclusive_benchmark_releases_gate(self):
        result = self.run_jobs(self.jobs([{"crash_in_benchmark": True}, {}, {}]))
        self.assertEqual(result["outcomes"][0]["status"], "failed")
        self.assertTrue(all(outcome["status"] == "completed" for outcome in result["outcomes"][1:]))

    def test_serial_slot_can_upgrade_from_cpu_baseline_to_simulated_cuda_job(self):
        jobs = self.jobs([{}, {}])
        jobs[1]["config"].update(device="cuda", topology_id="feature-mlp-16", topology={"family": "feature_mlp"})
        resources = {**self.resources, "cuda_available": True, "gpu_memory_bytes": 8 * 1024 ** 3,
                     "gpu_free_memory_bytes": 8 * 1024 ** 3}
        # Control-plane simulation only; numerical_control_runner does no CUDA work.
        result = execute_jobs(jobs, {"device": "auto", "execution": {"mode": "serial"}},
                              _runner=numerical_control_runner, _resources=resources)
        self.assertEqual([outcome["device"] for outcome in result["outcomes"]], ["cpu", "cuda"])
        self.assertEqual(result["outcomes"][0]["pid"], result["outcomes"][1]["pid"])
        self.assertTrue(all(outcome["status"] == "completed" for outcome in result["outcomes"]))

    def test_fail_fast_cancels_pending_jobs_and_uncooperative_peers(self):
        jobs = self.jobs([{"fail": True}, {"hang": True}, {}])
        started = time.monotonic()
        result = self.run_jobs(jobs, fail_fast=True)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result["outcomes"][0]["status"], "failed")
        self.assertEqual([outcome["status"] for outcome in result["outcomes"][1:]], ["cancelled", "cancelled"])

    def test_exhausted_gpu_and_ram_reserves_are_not_replaced_by_host_totals(self):
        resources = {**self.resources, "cuda_available": True, "gpu_memory_bytes": 8 * 1024 ** 3,
                     "gpu_free_memory_bytes": 0}
        plan = plan_execution({"device": "auto"}, resources)
        self.assertEqual(plan["resolved"]["gpu_workers"], 0)
        with self.assertRaisesRegex(ValueError, "RAM"):
            execute_jobs(self.jobs([{}]), {}, _runner=numerical_control_runner,
                         _resources={**self.resources, "available_ram_bytes": 0})

    def test_failed_trial_outcome_is_preserved_without_fake_report(self):
        result = self.run_jobs(self.jobs([{"fail": True}, {}]), axis="trials")
        failed = result["outcomes"][0]
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("report", failed)
        self.assertIn("deliberate", failed["error"])
        self.assertEqual(result["outcomes"][1]["status"], "completed")

    def test_deadline_terminates_uncooperative_native_work_and_recovers_slot(self):
        jobs = self.jobs([{"hang": True}, {}])
        jobs[0]["deadline"] = time.monotonic() + .3
        started = time.monotonic()
        result = execute_jobs(jobs, {"device": "cpu", "execution": {"mode": "serial"}},
                              _runner=numerical_control_runner, _resources=self.resources)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result["outcomes"][0]["status"], "timed_out")
        self.assertEqual(result["outcomes"][1]["status"], "completed")

    def test_parent_progress_cancellation_reaps_owned_spawn_processes(self):
        before = {child.pid for child in multiprocessing.active_children()}

        def cancel(update):
            if update["execution_progress"]["active_workers"]:
                raise RuntimeError("parent requested cancellation")

        with self.assertRaisesRegex(RuntimeError, "parent requested"):
            self.run_jobs(self.jobs([{}, {}]), progress=cancel)
        self.assertFalse({child.pid for child in multiprocessing.active_children()} - before)

    def test_duplicate_paths_and_expired_pending_jobs_are_safe(self):
        jobs = self.jobs([{}, {}])
        jobs[1]["output_dir"] = jobs[0]["output_dir"]
        with self.assertRaises(ValueError):
            self.run_jobs(jobs)
        jobs = self.jobs([{}])
        jobs[0]["deadline"] = time.monotonic() - 1
        result = self.run_jobs(jobs)
        self.assertEqual(result["outcomes"][0]["status"], "timed_out")
        self.assertIsNone(result["outcomes"][0]["pid"])


if __name__ == "__main__":
    unittest.main()
