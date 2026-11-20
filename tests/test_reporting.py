"""Portable report evidence, complete optimizer logs and honest partial results."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from signalclassifier.reporting import build_report_bundle


def csv_rows(archive, name):
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"))))


def child_report(identifier="cnn", name='Netz <script> & "A", Ω'):
    validation = {"accuracy": .9, "balanced_accuracy": .88, "macro_f1": .89,
                  "count": 20, "confusion_matrix": [[8, 2], [0, 10]]}
    trial = {"candidate": 1, "segment_length": 1024, "snr_db": 10, "jitter": 0,
             "accuracy": .9, "balanced_accuracy": .88, "macro_f1": .89,
             "count": 20, "confusion_matrix": [[8, 2], [0, 10]]}
    candidate = {"candidate": 1, "segment_length": 1024, "learning_rate": .001, "batch_size": 16,
                 "validation": validation, "validation_trials": [trial],
                 "history": [{"epoch": 1, "training_loss": .7, "validation_loss": .8},
                             {"epoch": 2, "training_loss": .4, "validation_loss": .5}],
                 "custom_candidate_field": {"all": [1, 2, 3]}}
    return {"topology": {"id": identifier, "name": name}, "classes": ["AM <audio>", 'FM & "radio"'],
            "evaluation_available": True, "evaluation_scope": "validation_and_test", "source": "synthetic", "synthetic": True,
            "config": {"seed": 17, "evaluation_mode": "independent", "snr_db": [10], "jitter": [0]},
            "validation": validation, "validation_trials": [trial], "candidates": [candidate], "selected_candidate": 1,
            "metrics": {"accuracy": .8, "balanced_accuracy": .8, "macro_f1": .8, "latency_ms_p95": 1.25,
                        "real_time_factor_p95": .1, "model_bytes": 3400, "training_seconds": 3.5},
            "hardware": {"training_device": "cuda", "inference_device": "cpu", "cpu": "Reference CPU"},
            "score": {"total": 70.5, "quality_score_available": True, "eligible_for_live": True,
                      "components": {"accuracy": 30, "robustness": 23, "realtime": 14, "training": 3.5}},
            "trials": [{**trial, "accuracy": .8, "balanced_accuracy": .8}],
            "confusion_matrix": [[7, 3], [1, 9]],
            "optimization": {"enabled": True, "method": "random", "objective": "validation_macro_f1", "seed": 17,
                             "max_trials": 3, "best_trial": 1, "best_config": {"segment_length": 1024},
                             "search_space": {"segment_lengths": [1024, 2048]}, "future_optimizer_field": ["retain", {"nested": True}],
                             "trials": [
                                 {"trial": 1, "status": "completed", "objective_value": .89, "validation": validation,
                                  "config": {"segment_length": 1024, "learning_rate": .001, "batch_size": 16, "epochs": 2},
                                  "elapsed_seconds": 3.5, "epoch_history": candidate["history"]},
                                 {"trial": 2, "status": "failed", "config": {"segment_length": 2048},
                                  "error": 'Treiber, "GPU"\nAusfall <x>', "elapsed_seconds": .2},
                                 {"trial": 3, "status": "timed_out", "config": {"segment_length": 2048},
                                  "epoch_history": [{"epoch": 1, "epoch_count": 4, "training_loss": .9, "validation_loss": 1.1}],
                                  "elapsed_seconds": 1, "partial_training_seconds": .8}],
                             "stop_reason": "timeout", "test_evaluations": 1}}


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def export(self, state, events=None, inventory=None):
        return build_report_bundle(state, self.root / "report.zip", events, inventory)

    def test_full_state_provenance_inventory_events_and_unknown_hpo_fields_survive(self):
        first = child_report()
        second = child_report("lda", "Referenz")
        comparison = [{"topology_id": report["topology"]["id"], "name": report["topology"]["name"],
                       "model_id": str(index), "score_rank": index + 1, "report": report,
                       "artifact_sha256": {"weights.pt": "payload-digest"}}
                      for index, report in enumerate((first, second))]
        state = {"id": "experiment-1", "name": "AM/FM-Versuch", "status": "completed", "created_at": "2026-11-20T12:00:00Z",
                 "config": {"source": "synthetic", "experiment_notes": 'Ziel, "Messaufbau"\n```\n<img src="remote">',
                            "future": {"keys": ["kept", 123]}},
                 "provenance": {"software": {"version": "0.3", "source_files_sha256": {"ml.py": "sourcehash"}},
                                "snapshot_hash_format": "canonical JSON", "configuration_sha256": "canonical-config-hash"},
                 "report": {**first, "comparison": comparison},
                 "partial_reports": [{"kind": "report", "report": first},
                                     {"kind": "optimization", "report": {**first["optimization"], "topology_id": "cnn"}}]}
        original = copy.deepcopy(state)
        events = [{"sequence": 1, "timestamp": "2026-11-20T12:00:01Z", "message": 'Epoch, "1"\nWeiter',
                   "data": {"loss": .8, "future_event": [1, 2]}}]
        inventory = [{"id": "r1", "group_id": "origin-group", "label": "AM", "verified": True,
                      "sample_rate": 256000, "metadata_sha256": "metadata", "iq_full_content_sha256": None,
                      "sigmf_metadata": {"global": {"core:description": 'A,"B"\nΩ'}}}]
        path = self.export(state, events, inventory)
        self.assertEqual(state, original, "export must not mutate experiment state")
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(json.loads(archive.read("report.json")), state)
            self.assertEqual(json.loads(archive.read("events.json")), events)
            self.assertEqual(json.loads(archive.read("dataset_inventory.json")), inventory)
            self.assertEqual(json.loads(archive.read("provenance.json")), state["provenance"])
            models = csv_rows(archive, "model_comparison.csv")
            self.assertEqual(len(models), 2, "selected mirrors and partial log mirrors are not additional models")
            self.assertEqual(models[0]["name"], first["topology"]["name"])
            self.assertEqual(models[0]["validation_balanced_accuracy"], "0.88")
            self.assertEqual(models[0]["test_balanced_accuracy"], "0.8")
            trials = csv_rows(archive, "optimization_trials.csv")
            self.assertEqual(len(trials), 6)
            self.assertEqual(trials[1]["error"], first["optimization"]["trials"][1]["error"])
            self.assertEqual(json.loads(trials[0]["raw_json"]), first["optimization"]["trials"][0])
            self.assertEqual(len(csv_rows(archive, "epoch_history.csv")), 6, "include the timed-out trial's completed epoch")
            self.assertEqual(csv_rows(archive, "events.csv")[0]["message"], events[0]["message"])
            document = archive.read("report.md").decode()
            self.assertIn(state["config"]["experiment_notes"], document)
            self.assertIn("Synthetischer Softwareversuch", document)
            self.assertIn("Testwerte sind ausdrücklich separate", document)
            self.assertNotIn("weights.pt", archive.namelist())

    def test_manifest_covers_actual_zip_bytes_and_every_svg_is_safe_xml(self):
        state = {"id": "a", "status": "completed", "report": child_report()}
        with zipfile.ZipFile(self.export(state)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(set(manifest["archive_members"]), set(archive.namelist()))
            self.assertEqual({row["path"] for row in manifest["files"]}, set(archive.namelist()) - {"manifest.json"})
            for row in manifest["files"]:
                content = archive.read(row["path"])
                self.assertEqual(row["bytes"], len(content))
                self.assertEqual(row["sha256"], hashlib.sha256(content).hexdigest())
            graphs = [name for name in archive.namelist() if name.endswith(".svg")]
            self.assertTrue(any("snr_jitter" in name for name in graphs))
            self.assertTrue(any("confusion" in name for name in graphs))
            for name in graphs:
                content = archive.read(name)
                root = ET.fromstring(content)
                for node in root.iter():
                    self.assertNotIn(node.tag.rsplit("}", 1)[-1], ("script", "foreignObject", "image"))
                    self.assertFalse(any(key.lower().startswith("on") or key.lower().endswith("href") for key in node.attrib))
                    for key in ("x", "y", "width", "height", "cx", "cy", "r"):
                        if key in node.attrib and not node.attrib[key].endswith("%"):
                            self.assertTrue(__import__("math").isfinite(float(node.attrib[key])))
                self.assertNotIn(b"<script>", content)

    def test_nonfinite_values_become_null_with_complete_path_and_marker_diagnostics(self):
        state = {"id": "nan", "status": "failed", "config": {"a/b~c": [float("nan"), float("inf"), float("-inf")]},
                 "report": {"metrics": {"accuracy": float("nan")}}}
        with zipfile.ZipFile(self.export(state, [{"loss": float("nan")}])) as archive:
            raw = archive.read("report.json").decode()
            parsed = json.loads(raw, parse_constant=lambda item: self.fail("JSON must not contain nonfinite tokens"))
            self.assertEqual(parsed["config"]["a/b~c"], [None, None, None])
            diagnostics = json.loads(archive.read("serialization.json"))["nonfinite_replacements"]
            self.assertIn("/config/a~1b~0c/0", {item["json_pointer"] for item in diagnostics})
            self.assertEqual(len(diagnostics), 5)
            self.assertEqual({item["original_value"] for item in diagnostics}, {"nan", "inf", "-inf"})
            self.assertFalse(any(name.endswith(".svg") for name in archive.namelist()))

    def test_training_only_keeps_raw_fields_without_presenting_unvalidated_quality(self):
        report = child_report()
        report.update(evaluation_available=False, validation_status="unvalidated")
        report["config"]["evaluation_mode"] = "training_only"
        state = {"status": "completed", "config": {"evaluation_mode": "training_only"}, "report": report}
        with zipfile.ZipFile(self.export(state)) as archive:
            self.assertEqual(json.loads(archive.read("report.json")), state)
            row = csv_rows(archive, "model_comparison.csv")[0]
            self.assertEqual(row["evaluation_available"], "false")
            for key in ("test_accuracy", "validation_accuracy", "score_total", "score_rank"):
                self.assertEqual(row[key], "")
            self.assertEqual(csv_rows(archive, "snr_jitter.csv"), [])
            self.assertEqual(csv_rows(archive, "confusion_matrices.csv"), [])
            self.assertEqual([name for name in archive.namelist() if name.endswith(".svg")], ["plots/model_timing.svg"])
            self.assertIn("Nur trainiert / unvalidiert", archive.read("report.md").decode())

    def test_cancelled_job_exports_direct_optimizer_partial_logs_and_epoch_history(self):
        optimization = child_report()["optimization"]
        optimization.update(topology_id="fb-lstm-16", status="interrupted")
        state = {"id": "cancelled", "status": "cancelled", "config": {"source": "recordings", "evaluation_mode": "independent"},
                 "error": "Abbruch durch Benutzer", "export_snapshot": {"legacy_warning": "No historical queue snapshot"},
                 "partial_reports": [{"path": "topology-1/optimization.json", "kind": "optimization", "report": optimization}]}
        events = [{"sequence": 1, "stage": "optimize", "data": {"trial": 2}}, {"sequence": 2, "stage": "cancelled"}]
        with zipfile.ZipFile(self.export(state, events)) as archive:
            self.assertEqual(len(csv_rows(archive, "optimization_trials.csv")), 3)
            self.assertEqual(len(csv_rows(archive, "epoch_history.csv")), 3)
            self.assertEqual(csv_rows(archive, "model_comparison.csv")[0]["score_total"], "")
            document = archive.read("report.md").decode()
            self.assertIn("Unvollständiger Lauf", document)
            self.assertIn("No historical queue snapshot", document)
            self.assertIn("Abbruch durch Benutzer", document)
            self.assertFalse(any(name.endswith(".svg") for name in archive.namelist()))
            self.assertEqual(json.loads(archive.read("events.json")), events)

    def test_failed_empty_job_and_atomic_destination_on_export_failure(self):
        state = {"id": "failed", "status": "failed", "error": 'Fehler, "Treiber"\nDetails', "config": {"device": "cuda"}}
        target = self.export(state)
        original = target.read_bytes()
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(csv_rows(archive, "model_comparison.csv"), [])
            self.assertIn(state["error"], archive.read("report.md").decode())
        with patch("signalclassifier.reporting.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.export({**state, "error": "new"})
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".report-bundle-*")), [])

    def test_report_inventory_omission_and_csv_source_pointers_are_explicit(self):
        report = child_report()
        state = {"status": "completed", "report": {"comparison": [{"topology_id": "cnn", "report": report}]}}
        inventory = [{"id": "r1", "group_id": "capture-1", "label": "AM", "metadata_omitted_reason": "32 MiB budget"}]

        def resolve(document, pointer):
            current = document
            for key in pointer.lstrip("/").split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                current = current[int(key)] if isinstance(current, list) else current[key]
            return current

        with zipfile.ZipFile(self.export(state, inventory=inventory)) as archive:
            for name in ("candidates.csv", "optimization_trials.csv", "epoch_history.csv", "snr_jitter.csv", "confusion_matrices.csv"):
                for row in csv_rows(archive, name):
                    self.assertIsNotNone(resolve(state, row["json_pointer"]), (name, row["json_pointer"]))
            self.assertIn("metadata_omitted_reason", archive.read("report.md").decode())

    def test_no_report_path_is_opened_and_validation_only_has_no_test_export(self):
        report = child_report()
        report.update(evaluation_scope="validation_only", model_path="/unreadable/model/weights.pt")
        state = {"status": "completed", "config": {"recordings": [{"id": "r", "path": "/unreadable/r.sigmf-meta", "group_id": "g"}]}, "report": report}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("report paths must never be opened")), \
             patch.object(Path, "read_text", side_effect=AssertionError("report paths must never be opened")):
            path = self.export(state)
        with zipfile.ZipFile(path) as archive:
            row = csv_rows(archive, "model_comparison.csv")[0]
            self.assertEqual(row["test_accuracy"], "")
            self.assertTrue(all(row["split"] == "validation" for row in csv_rows(archive, "snr_jitter.csv")))
            self.assertFalse(any("_test.svg" in name for name in archive.namelist()))
            self.assertEqual(json.loads(archive.read("dataset_inventory.json")), state["config"]["recordings"])

    def test_resource_observations_and_parallel_phase_times_export_without_zero_filling(self):
        report = child_report()
        report["phase_timings"] = {"training_epoch_seconds": .25, "benchmark_wait_seconds": .1}
        report["phase_timing_scope"] = "CUDA events for training; wall time for benchmark waits"
        report["optimization"]["trials"][0]["phase_timings"] = {"preprocessing_seconds": .2}
        report["execution"] = {"resolved": {"max_workers": 2}}
        report["resource_telemetry"] = {"sample_count": 2}
        events = [{"event": "resources", "timestamp_utc": "2026-11-20T12:00:00Z", "elapsed_seconds": 2,
                   "data": {"cpu": {"system_percent": 50, "process_percent": 200, "app_system_percent": 25},
                            "gpu": {"provider": "nvidia-smi", "scope": "whole_device", "devices": [{"index": 0, "name": "GPU", "utilization_percent": 0}]}}},
                  {"event": "resources", "elapsed_seconds": 4,
                   "data": {"cpu": {"system_percent": None}, "gpu": {"available": False, "reason": "not available", "devices": []}}}]
        with zipfile.ZipFile(self.export({"status": "completed", "report": report}, events=events)) as archive:
            resources = csv_rows(archive, "resource_usage.csv")
            self.assertEqual(resources[0]["gpu_utilization_percent"], "0")
            self.assertEqual(resources[0]["cpu_app_percent"], "200")
            self.assertEqual(resources[1]["gpu_utilization_percent"], "")
            self.assertEqual(resources[1]["cpu_system_percent"], "")
            phases = csv_rows(archive, "phase_timings.csv")
            self.assertEqual(len(phases), 3)
            self.assertTrue(any(row["scope"] == "optimization_trial" for row in phases))
            self.assertIn("Parallelisierung und gemessene Ressourcen", archive.read("report.md").decode())


if __name__ == "__main__":
    unittest.main()
