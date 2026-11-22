import base64
import copy
import hashlib
import io
import json
import os
import unittest
from unittest import mock
import urllib.error

from signalclassifier import publication as pub


RUN = "a" * 32
TOKEN = "ghp_" + "testcredential" * 4
SECRET = "PRIVATE_CANARY_7d1a90"


def fixture():
    topology = {"id": "feature-lda", "name": SECRET, "family": "feature_lda", "input": "features",
                "parameters": {"width": 10, "depth": 1, "notes": SECRET}, "layers": [SECRET]}
    quality = {"accuracy": .8, "balanced_accuracy": .8, "macro_f1": .8, "count": 10,
               "confusion_matrix": [[4, 1], [1, 4]], "recall": {"AM": .8, "FM": .8}, "hidden": SECRET}
    config = {"source": "recordings", "seed": 42, "evaluation_mode": "independent", "classes": ["AM", "FM"],
              "segment_length": 1024, "name": SECRET, "experiment_notes": SECRET, "recordings": [{"path": "/home/" + SECRET}],
              "github_publication": {"token": TOKEN}, "topologies": [topology]}
    candidate = {"candidate": 1, "segment_length": 1024, "learning_rate": .001, "batch_size": 16, "epochs": 2,
                 "validation": quality, "history": [{"epoch": 1, "training_loss": .4, "validation_loss": .5, "note": SECRET}],
                 "training_seconds": 2., "model_path": "/home/" + SECRET}
    report = {"topology": topology, "config": config, "classes": ["AM", "FM"], "validation": quality,
              "metrics": {**quality, "training_seconds": 2, "model_bytes": 1234, "path": "/home/" + SECRET},
              "evaluation_available": True, "test_evaluation_available": True, "evaluation_scope": "validation_and_test", "final_test_evaluations": 1,
              "candidates": [candidate, {**candidate, "candidate": 2, "segment_length": 2048}], "selected_candidate": 2,
              "history": candidate["history"], "split_groups": {"train": ["/private/group-" + SECRET], "validation": ["g2"], "test": ["g3"]},
              "trials": [{**quality, "candidate": 2, "snr_db": 10, "jitter": .05}],
              "validation_trials": [{**quality, "snr_db": 10, "jitter": 0}],
              "hardware": {"cpu": "Intel(R) Xeon(R) W-2135 " + SECRET, "gpu": "NVIDIA GeForce RTX 3070 " + SECRET,
                           "hostname": SECRET, "platform": "Linux-" + SECRET, "ram_bytes": 16000000000},
              "execution": {"resolved": {"max_workers": 4, "cpu_threads": 2, "ram_budget_bytes": 1000000000, "hostname": SECRET}, "resource_snapshot": {"private": SECRET}},
              "score_definition": {"version": 1, "maximum": 100, "weights": {"accuracy": 45, "robustness": 35, "realtime": 15, "training": 5}, "formulas": {"private": SECRET}},
              "optimization": {"enabled": True, "method": "random", "objective": "score", "trials": [
                  {"trial": 1, "status": "completed", "config": {"segment_length": 1024, "secret": SECRET},
                   "validation": quality, "objective_value": 95, "report": {"evaluation_scope": "validation_only", "final_test_evaluations": 0,
                       "split_groups": {"train": ["/private/group-" + SECRET]}, "secret": SECRET}, "epoch_history": candidate["history"]},
                  {"trial": 2, "status": "timed_out", "error": "Zeitbudget vor Start erreicht /home/" + SECRET + TOKEN}]}}
    experiment = {"id": RUN, "name": SECRET, "status": "completed", "config": config, "report": report,
                  "started_at": "2026-11-22T10:00:00+00:00", "finished_at": "2026-11-22T10:01:00+00:00",
                  "github_publication": {"token": TOKEN, "path": SECRET},
                  "provenance": {"settings_at_queue": {"station_lat": 49.9, "hostname": SECRET},
                      "software": {"version": "0.4.1", "git_revision": "b" * 40, "hostname": SECRET,
                          "dependencies": {"numpy": "2.5.5", SECRET: TOKEN},
                          "source_files_sha256": {"analysis_runtime.py": "1" * 64, "surveillance.py": "2" * 64, SECRET: "3" * 64}}}}
    inventory = [{"id": "/private/file-" + SECRET, "group_id": "/private/group-" + SECRET, "label": "AM", "verified": True,
                  "sample_rate": 25000, "center_frequency_hz": 125e6, "metadata_sha256": "4" * 64, "iq_full_content_sha256": None,
                  "metadata_filename": SECRET, "sigmf_metadata": {"global": {"token": TOKEN, "name": SECRET}},
                  "annotation": {"notes": SECRET}, "source": "recording"}]
    events = [{"event": "progress", "data": {"stage": "train", "epoch": 1, "training_loss": .4, "token": TOKEN,
                                              "nested": {"path": "/home/" + SECRET}}},
              {"event": SECRET, "data": {"message": TOKEN}}]
    return experiment, inventory, events


class FakeGitHub:
    def __init__(self, *, branch_exists=True, branch_race=False, update_races=0, already_exists=False):
        self.branch_exists, self.branch_race = branch_exists, branch_race
        self.update_races, self.already_exists = update_races, already_exists
        self.head = "a" * 40
        self.old_tree = "b" * 40
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if method == "GET" and "/git/" not in path:
            return {"default_branch": "main"}
        if method == "GET" and "/git/ref/heads/" in path:
            if path.endswith("main") or self.branch_exists:
                return {"object": {"sha": self.head}}
            raise pub.PublicationError("not_found")
        if method == "POST" and path.endswith("/git/refs"):
            self.branch_exists = True
            if self.branch_race:
                raise pub.PublicationError("conflict")
            return {"object": {"sha": self.head}}
        if method == "POST" and path.endswith("/git/blobs"):
            content = base64.b64decode(data["content"])
            return {"sha": hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()}
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": self.old_tree}}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": self.old_tree if self.already_exists else "c" * 40}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "e" * 40}
        if method == "PATCH":
            if self.update_races:
                self.update_races -= 1
                self.head = "d" * 40
                raise pub.PublicationError("conflict")
            self.head = data["sha"]
            return {"object": {"sha": self.head}}
        raise AssertionError((method, path))


class PublicationTests(unittest.TestCase):
    def package(self):
        return pub.make_publication_package(*fixture())

    def test_settings_are_opt_in_strict_and_credentials_are_environment_only(self):
        self.assertEqual(pub.normalize_publication(None), {"enabled": False, "repository": "schildtaero/SignalClassifier", "branch": "experiment-results"})
        for invalid in ({"token": TOKEN}, {"enabled": "yes"}, {"repository": "https://github.com/a/b"},
                        {"repository": "a/b/extra"}, {"branch": "main"}, {"branch": "../bad"},
                        {"branch": "foo.lock"}, {"branch": "a//b"}, {"branch": "a@{b"}, {"github_publication": {"enabled": True}}):
            with self.subTest(invalid=invalid), self.assertRaises(pub.PublicationError):
                pub.normalize_publication(invalid)
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}, clear=True):
            status = pub.credential_status()
            self.assertTrue(status["configured"])
            self.assertEqual(status["credential_source"], "environment")
            self.assertNotIn(TOKEN, json.dumps(status))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(pub.credential_status()["configured"])

    def test_nested_private_fields_never_enter_public_files_and_groups_stay_consistent(self):
        files = self.package()
        self.assertEqual(set(files), pub.PACKAGE_FILES)
        joined = b"".join(files.values()).decode()
        for forbidden in (SECRET, TOKEN, "/home/", "/private/", "sigmf_metadata", "metadata_filename", "station_lat", "hostname"):
            self.assertNotIn(forbidden, joined)
        data, dataset = json.loads(files["experiment.json"]), json.loads(files["dataset.json"])
        model = data["models"][0]
        self.assertEqual(model["split_groups"]["train"][0], dataset["recordings"][0]["group_alias"])
        self.assertEqual(dataset["recordings"][0]["center_frequency_hz"], 125e6)
        self.assertEqual(model["optimization"]["trials"][0]["split_groups"]["train"][0], dataset["recordings"][0]["group_alias"])
        self.assertEqual(model["test"]["confusion_matrix"], [[4, 1], [1, 4]])
        self.assertEqual(model["optimization"]["trials"][1]["error_category"], "budget_before_start")
        self.assertEqual(data["wall_seconds"], 60)
        self.assertEqual(model["hardware"]["cpu_model"], "Xeon W-2135")
        self.assertEqual(model["execution"]["resolved"]["max_workers"], 4)
        self.assertEqual(model["score_definition"]["formula_id"], "signalclassifier-score-v1")
        self.assertEqual(len(model["candidates"]), 2)
        self.assertEqual(model["selected_candidate"], 2)

    def test_manual_candidates_keep_all_curves_and_hpo_candidates_reference_existing_trials(self):
        experiment, inventory, events = fixture()
        experiment["report"]["optimization"] = {"enabled": False}
        model = json.loads(pub.make_publication_package(experiment, inventory, events)["experiment.json"])["models"][0]
        self.assertEqual(len(model["candidates"]), 2)
        self.assertEqual(model["candidates"][1]["config"]["segment_length"], 2048)
        self.assertEqual(model["candidates"][0]["history"][0]["training_loss"], .4)
        self.assertEqual(model["candidates"][0]["resource_metrics"]["training_seconds"], 2)
        experiment, inventory, events = fixture()
        model = json.loads(pub.make_publication_package(experiment, inventory, events)["experiment.json"])["models"][0]
        self.assertEqual(model["candidates"][0], {"candidate": 1, "optimization_trial": 1, "source": "optimization.trials_by_trial_number"})
        self.assertEqual(model["optimization"]["trials"][0]["epoch_history"][0]["training_loss"], .4)

    def test_known_fields_reject_nested_wrong_types_and_keep_known_subtypes(self):
        experiment, inventory, events = fixture()
        report = experiment["report"]
        report["metrics"]["accuracy"] = {"secret": SECRET, "token": TOKEN}
        report["topology"]["parameters"]["width"] = {"secret": SECRET}
        report["history"][0]["training_loss"] = {"secret": SECRET}
        report["score"] = {"components": {"accuracy": {"raw": {"secret": SECRET}}}}
        report["classes"] = ["2FSK", "4FSK", "BPSK", "QPSK", "16QAM", "64QAM", SECRET]
        files = pub.make_publication_package(experiment, inventory, events)
        self.assertNotIn(SECRET.encode(), b"".join(files.values()))
        model = json.loads(files["experiment.json"])["models"][0]
        self.assertNotIn("accuracy", model["test"])
        self.assertNotIn("width", model["topology"]["parameters"])
        self.assertEqual(model["classes"][:6], ["2FSK", "4FSK", "BPSK", "QPSK", "16QAM", "64QAM"])
        self.assertTrue(model["classes"][6].startswith("class-"))

    def test_package_is_deterministic_and_manifest_hashes_four_payloads(self):
        files = self.package()
        experiment, inventory, events = fixture()
        experiment["publication_status"] = {"updated_at": "later", "url": SECRET}
        self.assertEqual(files, pub.make_publication_package(experiment, inventory, events))
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(len(manifest["files"]), 4)
        for name, info in manifest["files"].items():
            self.assertEqual(info, {"bytes": len(files[name]), "sha256": hashlib.sha256(files[name]).hexdigest()})

    def test_optimizer_only_partial_resolves_architecture_without_faking_test(self):
        experiment, inventory, events = fixture()
        optimization = experiment.pop("report")["optimization"]
        optimization.update(topology_id="feature-lda", status="failed")
        experiment["partial_reports"] = [{"kind": "optimization", "path": SECRET, "report": optimization}]
        model = json.loads(pub.make_publication_package(experiment, inventory, events)["experiment.json"])["models"][0]
        self.assertEqual(model["topology"]["family"], "feature_lda")
        self.assertEqual(model["topology"]["parameters"]["width"], 10)
        self.assertIsNone(model["test"])
        self.assertEqual(model["test_robustness"], [])

    def test_training_only_never_publishes_quality_rank_or_objective(self):
        experiment, inventory, events = fixture()
        experiment["report"]["config"]["evaluation_mode"] = "training_only"
        model = json.loads(pub.make_publication_package(experiment, inventory, events)["experiment.json"])["models"][0]
        self.assertIsNone(model["validation"])
        self.assertIsNone(model["test"])
        self.assertIsNone(model["optimization"]["trials"][0]["objective_value"])

    def test_bounds_have_explicit_omission_counts_and_reject_oversized_packages(self):
        experiment, inventory, events = fixture()
        experiment["report"]["history"] *= 105
        data = json.loads(pub.make_publication_package(experiment, inventory, events)["experiment.json"])
        self.assertEqual(len(data["models"][0]["history"]), 100)
        self.assertGreater(data["omitted_counts"]["epochs"], 0)
        with mock.patch.object(pub, "MAX_PACKAGE_BYTES", 100), self.assertRaises(pub.PublicationError):
            self.package()

    def test_historical_benchmark_and_resource_series_use_only_typed_numeric_fields(self):
        experiment, inventory, events = fixture()
        experiment["report"]["benchmark"] = {"reference": "signalclassifier-features-v1/AM4096-fs256000-seed17",
                "reference_latency_ms": 1.5, "p95_ms": 2, "warmup": 3, "repetitions": 12, "batch_size": 1, "secret": SECRET}
        experiment["report"]["resource_telemetry"] = {"sample_count": 1,
                "cpu": {"system_percent": {"mean": 17, "maximum": 22, "sample_count": 1, "secret": SECRET}},
                "gpu_devices": [{"index": 0, "name": SECRET, "utilization_percent": {"mean": 12, "maximum": 20, "sample_count": 1}}]}
        events.append({"event": "resources", "elapsed_seconds": 2.5, "data": {"cpu": {"system_percent": 20,
                "process_percent": 120, "process_tree_rss_bytes": 12345, "memory_total_bytes": {"secret": SECRET}, "reason": SECRET},
                "gpu": {"available": True, "reason": SECRET, "devices": [{"index": 0, "name": SECRET,
                         "utilization_percent": None, "memory_used_bytes": 123456, "memory_total_bytes": 999999}]}}})
        files = pub.make_publication_package(experiment, inventory, events)
        data = json.loads(files["experiment.json"]); telemetry = json.loads(files["events-summary.json"])
        self.assertNotIn(SECRET.encode(), b"".join(files.values()))
        self.assertEqual(data["models"][0]["benchmark"]["reference_latency_ms"], 1.5)
        self.assertEqual(data["models"][0]["benchmark"]["reference_id"], experiment["report"]["benchmark"]["reference"])
        self.assertEqual(data["resource_telemetry"]["cpu"]["system_percent"]["mean"], 17)
        self.assertEqual(telemetry["event_counts"]["resources"], 1)
        sample = telemetry["resource_samples"][0]
        self.assertEqual(sample["elapsed_seconds"], 2.5)
        self.assertEqual(sample["cpu"]["process_percent"], 120)
        self.assertIsNone(sample["cpu"]["memory_total_bytes"])
        self.assertIsNone(sample["gpu"]["devices"][0]["utilization_percent"])
        self.assertEqual(sample["gpu"]["devices"][0]["memory_used_bytes"], 123456)

    def publish(self, api, files=None):
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_GITHUB_TOKEN": TOKEN}, clear=True), \
             mock.patch.object(pub, "_GitHub", return_value=api):
            return pub.publish_package({"enabled": True}, RUN, files or self.package())

    def test_branch_is_created_and_all_five_files_share_one_nonforced_commit(self):
        api = FakeGitHub(branch_exists=False)
        result = self.publish(api)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["file_count"], 5)
        self.assertEqual(len([x for x in api.calls if x[0] == "POST" and x[1].endswith("/git/commits")]), 1)
        updates = [x[2] for x in api.calls if x[0] == "PATCH"]
        self.assertEqual(updates, [{"sha": "e" * 40, "force": False}])
        self.assertTrue(all(path.startswith("paper-results/" + RUN + "/") for path in result["files"]))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_identical_tree_is_idempotent_without_an_extra_commit(self):
        api = FakeGitHub(already_exists=True)
        result = self.publish(api)
        self.assertTrue(result["already_exists"])
        self.assertFalse(any(method == "PATCH" or (method == "POST" and path.endswith("/git/commits")) for method, path, _ in api.calls))

    def test_branch_creation_and_fastforward_races_are_bounded_and_preserve_new_parent(self):
        api = FakeGitHub(branch_exists=False, branch_race=True, update_races=1)
        self.publish(api)
        commits = [data for method, path, data in api.calls if method == "POST" and path.endswith("/git/commits")]
        self.assertEqual(commits[-1]["parents"], ["d" * 40])
        self.assertEqual(sum(method == "PATCH" for method, _, _ in api.calls), 2)
        failing = FakeGitHub(update_races=10)
        with self.assertRaises(pub.PublicationError):
            self.publish(failing)
        self.assertEqual(sum(method == "PATCH" for method, _, _ in failing.calls), 3)

    def test_credentials_disabled_errors_and_private_files_never_trigger_upload(self):
        with mock.patch.object(pub, "_GitHub") as api:
            self.assertEqual(pub.publish_package(None, RUN, {}), {"status": "disabled"})
            api.assert_not_called()
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(pub.PublicationError):
            pub.publish_package({"enabled": True}, RUN, self.package())
        files = self.package(); files["private.sigmf-data"] = b"IQ"
        with mock.patch.object(pub, "_GitHub") as api, self.assertRaises(pub.PublicationError):
            self.publish(FakeGitHub(), files)
        api.assert_not_called()
        self.assertNotIn(SECRET, pub.safe_error(RuntimeError(SECRET + TOKEN)))
        self.assertNotIn(TOKEN, pub.safe_error(RuntimeError(SECRET + TOKEN)))

    def test_actual_http_wrapper_uses_fixed_https_no_redirect_and_bounded_response(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"sha":"' + b"a" * 40 + b'"}'
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.object(pub.urllib.request, "build_opener", return_value=opener) as build:
            api = pub._GitHub(TOKEN)
            api.request("GET", "/repos/schildtaero/SignalClassifier")
        self.assertIsInstance(build.call_args.args[0], pub._NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/schildtaero/SignalClassifier")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertLessEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(response.read.call_args.args[0], 2 * 1024**2 + 1)
        self.assertIsNone(pub._NoRedirect().redirect_request(None, None, 301, "moved", {}, "https://evil.invalid"))
        opener.open.side_effect = urllib.error.HTTPError("https://api.github.com", 403, TOKEN, {}, io.BytesIO(TOKEN.encode()))
        with mock.patch.object(pub.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(pub.PublicationError) as error:
                pub._GitHub(TOKEN).request("GET", "/repos/schildtaero/SignalClassifier")
        self.assertNotIn(TOKEN, str(error.exception))


if __name__ == "__main__":
    unittest.main()
