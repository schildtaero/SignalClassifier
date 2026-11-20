"""Telemetry measures process trees/devices without pretending missing data is zero."""
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from signalclassifier.telemetry import ResourceSummary, ResourceTelemetry, nvidia_sample, parse_nvidia_csv


class TelemetryTests(unittest.TestCase):
    def test_nvidia_csv_preserves_zero_unknowns_and_quoted_device_names(self):
        rows = parse_nvidia_csv('0, "NVIDIA, RTX", 0, 128, 8192\n1, Other, [Not Supported], N/A, 4096\nbad, bad, bad, bad, bad\nshort,row\n')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "NVIDIA, RTX")
        self.assertEqual(rows[0]["utilization_percent"], 0.)
        self.assertEqual(rows[0]["memory_used_bytes"], 128 * 1024**2)
        self.assertIsNone(rows[1]["utilization_percent"])
        self.assertIsNone(rows[1]["memory_used_bytes"])
        self.assertIsNone(parse_nvidia_csv('0, GPU, nan, -1, inf')[0]["utilization_percent"])

    def test_query_unavailable_timeout_and_partial_wsl_support_are_explicit(self):
        with patch("signalclassifier.telemetry.subprocess.run") as run:
            self.assertFalse(nvidia_sample(None)["available"])
            run.assert_not_called()
            run.return_value = SimpleNamespace(returncode=0, stdout="0, GPU, N/A, 100, 8192", stderr="")
            result = nvidia_sample("/mock/nvidia-smi")
            self.assertTrue(result["available"])
            self.assertEqual(result["scope"], "whole_device")
            self.assertIsNone(result["devices"][0]["utilization_percent"])
            self.assertTrue(result["reason"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.5)
            run.side_effect = subprocess.TimeoutExpired("nvidia-smi", 1.5)
            self.assertFalse(nvidia_sample("/mock/nvidia-smi")["available"])

    def test_cpu_sampler_includes_child_workers_and_normalizes_system_capacity(self):
        parent, child = Mock(), Mock()
        parent.pid, child.pid = 1, 2
        parent.create_time.return_value = child.create_time.return_value = 1.
        parent.children.return_value = [child]
        parent.cpu_percent.side_effect, child.cpu_percent.side_effect = [0., 50.], [0., 150.]
        parent.memory_info.return_value, child.memory_info.return_value = SimpleNamespace(rss=100), SimpleNamespace(rss=200)
        telemetry = ResourceTelemetry.__new__(ResourceTelemetry)
        telemetry._parent, telemetry._processes, telemetry._system_primed = parent, {}, False
        with patch("signalclassifier.telemetry.psutil.cpu_percent", side_effect=[0., 75.]), \
             patch("signalclassifier.telemetry.psutil.virtual_memory", return_value=SimpleNamespace(total=10000, available=5000)), \
             patch("signalclassifier.telemetry.os.cpu_count", return_value=4), \
             patch("signalclassifier.telemetry.effective_cpu_capacity", return_value=4.):
            self.assertIsNone(telemetry._cpu_sample()["process_percent"])
            sample = telemetry._cpu_sample()
        self.assertEqual(sample["process_percent"], 200.)
        self.assertEqual(sample["app_system_percent"], 50.)
        self.assertEqual(sample["system_percent"], 75.)
        self.assertEqual(sample["process_count"], 2)
        self.assertEqual(sample["process_tree_rss_bytes"], 300)

    def test_summary_uses_only_real_observations(self):
        summary = ResourceSummary()
        summary.add({"sampled_at": "first", "cpu": {"process_percent": 200}, "gpu": {"devices": [{"index": 0, "name": "GPU", "utilization_percent": None}]}})
        summary.add({"sampled_at": "second", "cpu": {"process_percent": 0}, "gpu": {"devices": [{"index": 0, "name": "GPU", "utilization_percent": 0}]}})
        summary.add({"sampled_at": "last", "cpu": {}, "gpu": {"devices": [{"index": 0, "name": "GPU", "utilization_percent": 50}]}})
        result = summary.snapshot()
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["cpu"]["process_percent"]["mean"], 100.)
        self.assertEqual(result["gpu_devices"][0]["utilization_percent"], {"mean": 25., "maximum": 50., "sample_count": 2})
        self.assertNotIn("memory_used_bytes", result["gpu_devices"][0])

    def test_snapshots_are_cached_copied_and_staleness_is_visible(self):
        ready = threading.Event()
        with patch("signalclassifier.telemetry.nvidia_sample", return_value={"available": True, "devices": [{"index": 0, "name": "mock", "utilization_percent": 0}], "provider": "nvidia-smi", "scope": "whole_device", "reason": None}) as query:
            monitor = ResourceTelemetry(interval_seconds=10, on_sample=lambda sample: ready.set())
            try:
                self.assertTrue(ready.wait(2))
                first = monitor.snapshot()
                first["gpu"]["devices"][0]["name"] = "changed by caller"
                second = monitor.snapshot()
                self.assertEqual(second["gpu"]["devices"][0]["name"], "mock")
                self.assertTrue(second["cached"])
                self.assertFalse(second["stale"])
                self.assertEqual(query.call_count, 1, "HTTP snapshots must never run nvidia-smi")
                with monitor._lock:
                    monitor._last_sample = time.monotonic() - 60
                self.assertTrue(monitor.snapshot()["stale"])
            finally:
                monitor.close()
            self.assertFalse(monitor._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
