"""Public sampling evidence stays typed and cannot validate a training-only run."""

import copy
import json
import unittest

from signalclassifier import publication
from tests.test_publication import fixture


SECRET = "PRIVATE_SAMPLING_CANARY_8a7c2"
SUMMARY = {"policy": "all_recordings_fair_quota_v1", "recordings": 240,
           "segments": 4096, "retained_iq_bytes": 134217728, "budget_iq_bytes": 134217728,
           "capped": True, "all_recordings_represented": True, "anchor_samples": 4096,
           "available_segments": 400000, "segment_limit": 4096}


def training_fixture():
    experiment, inventory, events = fixture()
    experiment["config"].update(source="recordings", evaluation_mode="training_only", use_all_recordings=True)
    report = experiment["report"]
    report.update(use_all_recordings=True, recording_sampling=copy.deepcopy(SUMMARY),
                  evaluation_available=False, test_evaluation_available=False,
                  evaluation_scope="training_only", validation_status="unvalidated",
                  score={"total": 99}, score_rank=1, accuracy_rank=1)
    return experiment, inventory, events


def package_data(experiment, inventory, events):
    return json.loads(publication.make_publication_package(experiment, inventory, events)["experiment.json"])


class PublicationTrainingSamplingTests(unittest.TestCase):
    def test_known_sampling_summary_and_consent_survive_without_quality_or_ranking(self):
        experiment, inventory, events = training_fixture()
        experiment["provenance"]["software"]["source_files_sha256"]["training_plan.py"] = "9" * 64
        data = package_data(experiment, inventory, events)
        self.assertEqual(data["software"]["source_files_sha256"]["training_plan.py"], "9" * 64)
        self.assertIs(data["config"]["use_all_recordings"], True)
        self.assertIs(data["use_all_recordings"], True)
        self.assertEqual(data["recording_sampling"], SUMMARY)
        model = data["models"][0]
        self.assertIs(model["config"]["use_all_recordings"], True)
        self.assertIs(model["use_all_recordings"], True)
        self.assertEqual(model["recording_sampling"], SUMMARY)
        self.assertEqual(model["validation_independence"]["split_policy"], "training_only")
        self.assertIsNone(model["validation"])
        self.assertIsNone(model["test"])
        self.assertIsNone(model["score"])
        self.assertIsNone(model["score_rank"])
        self.assertIsNone(model["accuracy_rank"])
        self.assertEqual(model["validation_robustness"], [])
        self.assertEqual(model["test_robustness"], [])
        for trial in model["optimization"]["trials"]:
            self.assertIsNone(trial["objective_value"])
            self.assertIsNone(trial["validation"])
            self.assertIsNone(trial["score"])

    def test_false_flags_are_preserved_and_non_booleans_are_never_coerced(self):
        for flag in (True, False):
            experiment, inventory, events = training_fixture()
            experiment["config"]["use_all_recordings"] = flag
            experiment["report"]["use_all_recordings"] = flag
            experiment["report"]["recording_sampling"].update(capped=flag, all_recordings_represented=flag)
            data = package_data(experiment, inventory, events)
            for view in (data, data["models"][0]):
                self.assertIs(view["config"]["use_all_recordings"], flag)
                self.assertIs(view["use_all_recordings"], flag)
                self.assertIs(view["recording_sampling"]["capped"], flag)
                self.assertIs(view["recording_sampling"]["all_recordings_represented"], flag)
        for invalid in (None, 0, 1, 1.0, "true", [True], {"token": SECRET}):
            with self.subTest(invalid=invalid):
                experiment, inventory, events = training_fixture()
                experiment["config"]["use_all_recordings"] = invalid
                experiment["report"]["use_all_recordings"] = invalid
                experiment["report"]["recording_sampling"].update(capped=invalid, all_recordings_represented=invalid)
                data = package_data(experiment, inventory, events)
                for view in (data, data["models"][0]):
                    self.assertNotIn("use_all_recordings", view["config"])
                    self.assertNotIn("use_all_recordings", view)
                    self.assertNotIn("capped", view["recording_sampling"])
                    self.assertNotIn("all_recordings_represented", view["recording_sampling"])

    def test_unknown_fields_and_nested_payloads_are_removed_from_every_public_file(self):
        experiment, inventory, events = training_fixture()
        summary = experiment["report"]["recording_sampling"]
        summary.update(recording_ids=[SECRET], paths=["/private/" + SECRET], iq=[SECRET],
                       notes=SECRET, token=SECRET, nested={"secret": SECRET})
        experiment["config"]["recording_sampling"] = {"uri": SECRET}
        files = publication.make_publication_package(experiment, inventory, events)
        joined = b"".join(files.values()).decode()
        self.assertNotIn(SECRET, joined)
        self.assertEqual(json.loads(files["experiment.json"])["recording_sampling"], SUMMARY)
        for key in SUMMARY:
            experiment, inventory, events = training_fixture()
            experiment["report"]["recording_sampling"][key] = {"path": "/private/" + SECRET, "iq": [SECRET]}
            files = publication.make_publication_package(experiment, inventory, events)
            self.assertNotIn(SECRET, b"".join(files.values()).decode())

    def test_counts_require_nonnegative_integers_and_policy_is_a_closed_enum(self):
        projector = publication._Projection("sampling-audit")
        for key in ("recordings", "segments", "retained_iq_bytes", "budget_iq_bytes",
                    "anchor_samples", "available_segments", "segment_limit"):
            for invalid in (True, False, 2.0, -1, float("inf"), float("nan"), 10**16, "2", [2], {"path": SECRET}):
                with self.subTest(key=key, value=invalid):
                    self.assertNotIn(key, projector.recording_sampling({**SUMMARY, key: invalid}))
            self.assertEqual(projector.recording_sampling({**SUMMARY, key: 0})[key], 0)
        self.assertEqual(projector.recording_sampling({**SUMMARY, "policy": SECRET})["policy"], "unknown")

    def test_missing_or_non_object_summary_is_unknown_without_inventing_a_default(self):
        for invalid in (None, [], SECRET, 1, True, {}):
            with self.subTest(invalid=invalid):
                experiment, inventory, events = training_fixture()
                experiment["report"]["recording_sampling"] = invalid
                data = package_data(experiment, inventory, events)
                self.assertIsNone(data["recording_sampling"])
                self.assertIsNone(data["models"][0]["recording_sampling"])
        experiment, inventory, events = fixture()
        data = package_data(experiment, inventory, events)
        self.assertNotIn("use_all_recordings", data["config"])
        self.assertIsNone(data["recording_sampling"])

    def test_comparison_preserves_each_model_sampling_without_fabricating_a_combined_summary(self):
        experiment, inventory, events = training_fixture()
        first = experiment["report"]
        second = copy.deepcopy(first)
        second["topology"]["id"] = "feature-mlp-16"
        second["recording_sampling"]["segments"] = 2048
        experiment["report"] = {"comparison": [{"topology_id": "feature-lda", "report": first},
                                                {"topology_id": "feature-mlp-16", "report": second}]}
        data = package_data(experiment, inventory, events)
        self.assertIsNone(data["recording_sampling"])
        self.assertEqual([row["recording_sampling"]["segments"] for row in data["models"]], [4096, 2048])
        self.assertTrue(all(row["score"] is None and row["validation"] is None for row in data["models"]))


if __name__ == "__main__":
    unittest.main()
