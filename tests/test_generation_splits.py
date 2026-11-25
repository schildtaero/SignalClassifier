import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from signalclassifier import ml, sigmf
from signalclassifier.sigmf import SigMFWriter
from tests.test_ml import inline_execution


class GenerationSplitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        ml._recording_content_digest.cache_clear()
        self.entries = []
        for label_index, label in enumerate(("AM", "FM")):
            for index, split in enumerate(("train", "validation", "test")):
                root = f"payload-{label}-{index}"
                self.entries.append(self.recording(label, index + 10 * label_index,
                                                   root=root, split=split))

    def recording(self, label, seed, *, root, split, identifier=None):
        identifier = identifier or root
        metadata = {"sc:origin_recording_id": root,
                    "signalclassifier:group_id": "one-physical-lab-session",
                    "sc:generation": {"campaign_id": "one-physical-lab-session",
                                      "payload_root_id": root, "planned_split": split,
                                      "label_facet": "modulation",
                                      "facets": {"waveform_family": "single_carrier", "modulation": label,
                                                 "protocol": None, "service": None, "emitter": None},
                                      "reference_kind": "controlled_generator_reference",
                                      "rf_origin_verified": False}}
        writer = SigMFWriter(self.root / identifier, 256000, 100e6, extra=metadata)
        writer.append(ml.generate_signal(label, length=1024, seed=seed))
        path = writer.close()
        return {"id": identifier, "metadata_path": str(path), "group_id": "one-physical-lab-session",
                "label": label, "verified": True}

    def config(self, **values):
        return {"source": "recordings", "recordings": self.entries, "segment_length": 256,
                "samples_per_class": 12, "snr_db": [20], "jitter": [0], "epochs": 1,
                "batch_size": 4, "device": "cpu", "allow_same_session": True,
                "respect_generation_splits": True, **values}

    def mutate(self, entry, operation):
        path = Path(entry["metadata_path"])
        doc = json.loads(path.read_text())
        operation(doc["global"])
        path.write_text(json.dumps(doc))

    def test_flag_is_strict_opt_in_requires_recordings_and_session_consent(self):
        baseline = ml._validate_config(self.config(respect_generation_splits=False))
        self.assertFalse(baseline["respect_generation_splits"])
        self.assertEqual(ml._validate_config({})["respect_generation_splits"], False)
        for changes in ({"respect_generation_splits": "true"}, {"respect_generation_splits": 1},
                        {"allow_same_session": False}, {"source": "synthetic"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ml._validate_config(self.config(**changes))
                self.assertFalse(ml.dataset_readiness(self.config(**changes))["ready"])

    def test_fixed_splits_are_shared_by_preflight_loader_and_hpo_plan_without_random_assignment(self):
        config = ml._validate_config(self.config())
        with mock.patch.object(ml, "_assign_groups", side_effect=AssertionError("must not re-randomize planned groups")), \
             mock.patch.object(sigmf, "read_recording", side_effect=AssertionError("preflight must not decode numeric IQ")):
            ready = ml.dataset_readiness(config)
            planned = ml._planned_split_groups(config)
        self.assertTrue(ready["ready"], ready["errors"])
        self.assertEqual(ready["split_source"], "generator_plan")
        self.assertEqual(ready["split_groups"], planned)
        self.assertEqual(ready["generator_reference_summary"], {
            "count": 6, "facets": ["modulation"], "reference_kind": "controlled_generator_reference",
            "reference_kinds": ["controlled_generator_reference"], "rx_class_verified": False, "rf_origin_verified": False})
        self.assertEqual(ready["label_evidence"], "generator_provenance")
        self.assertTrue(all(row["session_groups"] == 1 for row in ready["classes"]))
        for seed in (1, 42, 987):
            groups = ml._recording_groups({**config, "seed": seed}, 256)
            observed = {split: sorted({entry["group_id"] for entry in rows}) for split, rows in groups.items()}
            self.assertEqual(observed, planned)
            for split, rows in groups.items():
                self.assertTrue(all(entry["recording_group_id"].endswith({"train": "0", "validation": "1", "test": "2"}[split]) for entry in rows))

    def test_declared_origin_variant_conflict_is_rejected_even_in_training_only(self):
        variant = self.recording("AM", 99, root="payload-AM-0", split="test", identifier="conflicting-variant")
        config = self.config(recordings=self.entries + [variant])
        for mode in ("independent", "training_only"):
            candidate = {**config, "evaluation_mode": mode}
            ready = ml.dataset_readiness(candidate)
            self.assertFalse(ready["ready"])
            self.assertTrue(any("Splitkonflikt" in error for error in ready["errors"]), ready["errors"])
            with self.assertRaisesRegex(ValueError, "Splitkonflikt"):
                ml._recording_groups(ml._validate_config(candidate), 256)

    def test_exact_duplicate_with_forged_new_payload_root_cannot_cross_splits(self):
        entry = self.entries[0]
        path = self.root / "forged-copy.sigmf-meta"
        doc = json.loads(Path(entry["metadata_path"]).read_text())
        doc["global"]["sc:recording_id"] = "forged-copy"
        doc["global"]["sc:origin_recording_id"] = "forged-origin"
        doc["global"]["sc:generation"].update(payload_root_id="forged-origin", planned_split="test")
        path.write_text(json.dumps(doc))
        shutil.copyfile(Path(entry["metadata_path"]).with_suffix(".sigmf-data"), path.with_suffix(".sigmf-data"))
        copied = {**entry, "id": "forged-copy", "metadata_path": str(path)}
        result = ml.dataset_readiness(self.config(recordings=self.entries + [copied]))
        self.assertFalse(result["ready"])
        self.assertTrue(any("Splitkonflikt" in error for error in result["errors"]), result["errors"])

    def test_generation_payload_root_unites_variants_even_if_capture_origin_was_mislabeled(self):
        variant = self.recording("AM", 99, root="payload-AM-0", split="test", identifier="separate-capture")
        self.mutate(variant, lambda glob: glob.update({"sc:origin_recording_id": "wrong-separate-capture-root"}))
        result = ml.dataset_readiness(self.config(recordings=self.entries + [variant]))
        self.assertFalse(result["ready"])
        self.assertTrue(any("Splitkonflikt" in error for error in result["errors"]))

    def test_missing_malformed_mixed_facets_and_mismatched_labels_never_fall_back(self):
        mutations = [lambda glob: glob.pop("sc:generation"),
                     lambda glob: glob.update({"sc:generation": "not-an-object"}),
                     lambda glob: glob["sc:generation"].pop("planned_split"),
                     lambda glob: glob["sc:generation"].update(planned_split="other"),
                     lambda glob: glob["sc:generation"].update(payload_root_id=None),
                     lambda glob: glob["sc:generation"].update(reference_kind="human-confirmed"),
                     lambda glob: glob["sc:generation"]["facets"].update(modulation="FM"),
                     lambda glob: glob["sc:generation"].update(label_facet="protocol", facets={"protocol": "AM"})]
        path = Path(self.entries[0]["metadata_path"])
        original = path.read_text()
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                path.write_text(original)
                self.mutate(self.entries[0], mutation)
                ready = ml.dataset_readiness(self.config())
                self.assertFalse(ready["ready"], ready)
                with self.assertRaises(ValueError):
                    ml._planned_split_groups(ml._validate_config(self.config()))
        path.write_text(original)

    def test_fixed_templates_train_only_cannot_be_reassigned_into_validation_or_test(self):
        for entry in self.entries:
            self.mutate(entry, lambda glob: glob["sc:generation"].update(planned_split="train"))
        result = ml.dataset_readiness(self.config())
        self.assertFalse(result["ready"])
        self.assertTrue(any("validation" in error and "Frische" in error for error in result["errors"]), result["errors"])
        train_config = ml._validate_config(self.config(recordings=[self.entries[0], self.entries[3]], evaluation_mode="training_only"))
        ready = ml.dataset_readiness(train_config)
        self.assertTrue(ready["ready"], ready["errors"])
        report = ml.run_experiment(train_config, self.root / "only-train")
        self.assertIsNone(report["metrics"]["accuracy"])
        self.assertFalse(report["evaluation_available"])
        self.assertEqual(report["trials"], [])
        self.assertEqual(report["split_groups"]["test"], [])
        self.assertEqual(report["split_groups"]["validation"], [])
        self.assertEqual(report["label_evidence"], "generator_provenance")
        manifest = ml.load_model_metadata(report["model_path"])
        self.assertEqual(manifest["split_source"], "generator_plan")
        self.assertEqual(manifest["provenance"]["label_evidence"], "generator_provenance")
        self.assertFalse(manifest["generator_reference_summary"]["rx_class_verified"])

    def test_opt_out_preserves_group_assignment_and_warns_for_mixed_generator_references(self):
        self.mutate(self.entries[0], lambda glob: glob.pop("sc:generation"))
        config = ml._validate_config(self.config(respect_generation_splits=False))
        original = ml._assign_groups
        with mock.patch.object(ml, "_assign_groups", wraps=original) as assignment:
            ready = ml.dataset_readiness(config)
            groups = ml._recording_groups(config, 256)
        self.assertTrue(ready["ready"], ready["errors"])
        self.assertEqual(assignment.call_count, 2)
        self.assertEqual(ready["split_source"], "group_assignment")
        self.assertEqual(ready["generator_reference_summary"]["count"], 5)
        self.assertEqual(ready["label_evidence"], "mixed_generator_and_recording_annotations")
        self.assertTrue(any("nicht übernommen" in warning for warning in ready["warnings"]))
        self.assertEqual({split: sorted({entry["group_id"] for entry in rows}) for split, rows in groups.items()}, ready["split_groups"])

    def test_hpo_preserves_plan_reference_scope_and_does_not_access_test_features_during_search(self):
        config = self.config(optimization={"enabled": True, "max_trials": 2, "timeout_seconds": 0,
                             "search_space": {"segment_lengths": [256, 512], "batch_sizes": [4], "epochs": [1]}})
        test_paths = {entry["metadata_path"] for entry in (self.entries[2], self.entries[5])}
        read, evaluate = sigmf.read_recording, ml._evaluate_optimized_test
        phase = {"final": False, "test_reads": 0}
        def guarded_read(path, *args, **kwargs):
            if str(path) in test_paths:
                self.assertTrue(phase["final"], "test features accessed before winner fixed")
                phase["test_reads"] += 1
            return read(path, *args, **kwargs)
        def final(*args, **kwargs):
            phase["final"] = True
            return evaluate(*args, **kwargs)
        with mock.patch.object(ml, "execute_jobs", side_effect=inline_execution), \
             mock.patch.object(sigmf, "read_recording", side_effect=guarded_read), \
             mock.patch.object(ml, "_evaluate_optimized_test", side_effect=final):
            report = ml.run_experiment(config, self.root / "optimization")
        self.assertEqual(phase["test_reads"], 2)
        self.assertTrue(report["respect_generation_splits"])
        self.assertEqual(report["split_source"], "generator_plan")
        self.assertEqual(report["label_evidence"], "generator_provenance")
        self.assertFalse(report["generator_reference_summary"]["rx_class_verified"])
        self.assertFalse(report["validation_independence"]["session_independent"])
        self.assertTrue(any("Generator-Referenzlabels" in text for text in report["limitations"]))
        self.assertEqual(report["optimization"]["split_source"], "generator_plan")
        self.assertEqual(report["optimization"]["generator_reference_summary"], report["generator_reference_summary"])
        for trial in report["optimization"]["trials"]:
            self.assertEqual(trial["status"], "completed")
            self.assertEqual(trial["report"]["split_groups"], report["split_groups"])
            self.assertEqual(trial["report"]["evaluation_scope"], "validation_only")
            self.assertEqual(trial["report"]["label_evidence"], "generator_provenance")
        manifest = ml.load_model_metadata(report["model_path"])
        self.assertEqual(manifest["generator_reference_summary"], report["generator_reference_summary"])
        self.assertEqual(manifest["split_groups"], report["split_groups"])
        self.assertEqual(report["final_test_evaluations"], 1)

    def test_frozen_hpo_group_plan_cannot_be_silently_replaced(self):
        config = ml._validate_config(self.config())
        planned = copy.deepcopy(ml._planned_split_groups(config))
        planned["train"], planned["test"] = planned["test"], planned["train"]
        with mock.patch.object(sigmf, "read_recording", side_effect=AssertionError("must fail before IQ evaluation")):
            with self.assertRaisesRegex(ValueError, "seit der Suchplanung verändert"):
                ml.run_experiment(config, self.root / "changed-plan", _evaluate_test=False, _planned_groups=planned)


if __name__ == "__main__":
    unittest.main()
