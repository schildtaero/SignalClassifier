import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from signalclassifier import decision, ml
from signalclassifier.topologies import validate_topology


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        # An elementary physical AM fixture; no network training in these tests.
        t = np.arange(2048)
        self.iq = ((1 + .4 * np.sin(.03 * t)) * np.exp(.17j * t)).astype(np.complex64)

    def model(self, name, classes=("AM", "FM"), winner=0, evaluated=True, length=1024):
        path = Path(self.root.name) / name
        path.mkdir()
        topology = validate_topology({"id": "feature-lda"})
        manifest = {"schema_version": 1, "classes": list(classes), "family": "feature_lda",
                    "input": "features", "topology": topology, "topology_id": topology["id"],
                    "weights_file": "weights.npz", "segment_length": length, "sample_rate": 256000.,
                    "sample_rates": [256000.], "evaluation_available": evaluated,
                    "validation_status": "evaluated" if evaluated else "unvalidated",
                    "confidence_threshold": .6}
        (path / "model.json").write_text(json.dumps(manifest))
        bias = np.zeros(len(classes), dtype=np.float32)
        bias[winner] = 5
        np.savez(path / "weights.npz", weights=np.zeros((len(classes), 10), dtype=np.float32),
                 bias=bias, mean=np.zeros(10), scale=np.ones(10))
        return {"id": name, "name": name, "model_path": str(path)}

    def analyze(self, **kwargs):
        policy = kwargs.pop("policy", {"budget_ms": 0})
        return decision.analyze_signal(self.iq, 256000, policy=policy, **kwargs)

    def test_frequency_and_prior_hints_cannot_change_model_or_routing(self):
        model = self.model("baseline")
        a = self.analyze(models=[model], center_frequency_hz=100e6,
                         prior_hints=[{"label": "FM", "reason": "band"}])
        b = self.analyze(models=[model], center_frequency_hz=125e6,
                         prior_hints=[{"label": "AM", "reason": "band"}])
        self.assertEqual(a["conclusions"], b["conclusions"])
        self.assertEqual(a["hypotheses"], b["hypotheses"])
        self.assertEqual(a["predictions"][0]["probabilities"], b["predictions"][0]["probabilities"])
        self.assertEqual(a["routes"][0]["priority"], b["routes"][0]["priority"])
        self.assertFalse(a["prior_hints"]["used_for_decision"])
        self.assertEqual(a["conclusions"]["modulation"]["status"], "candidate")
        self.assertFalse(a["predictions"][0]["ood_validated"])
        self.assertEqual(a["routes"][0]["model_hash"]["files"]["model.json"],
                         hashlib.sha256((Path(model["model_path"]) / "model.json").read_bytes()).hexdigest())
        self.assertEqual([a["id"] for a in a["stages"]],
                         ["quality", "features", "hypotheses", "routing", "models", "fusion"])

    def test_features_alone_are_hypotheses_not_modulation_or_service_proof(self):
        result = self.analyze()
        self.assertTrue(result["hypotheses"])
        self.assertEqual(result["status"], "unknown")
        for level in ("modulation", "protocol", "service", "emitter"):
            self.assertIsNone(result["conclusions"][level]["label"])
        self.assertEqual(result["conclusions"]["waveform_family"]["status"], "hypothesis")
        self.assertTrue(all(h["score"] < 1 for h in result["hypotheses"]))

    def test_quality_gates_skip_weights_but_keep_explained_routes(self):
        model = {"id": "missing", "model_path": "missing"}
        with mock.patch.object(ml, "load_model_metadata", side_effect=AssertionError("metadata must not load")):
            for iq, status in ((np.zeros(1024), "no_signal"), (np.ones(100), "insufficient_samples"),
                               (np.array([np.nan] * 512), "invalid")):
                result = decision.analyze_signal(iq, 256000, models=[model])
                self.assertEqual(result["status"], status)
                self.assertEqual(result["budget"]["models_run"], 0)
                self.assertEqual(result["routes"][0]["status"], "skipped")
                self.assertTrue(result["routes"][0]["why"])
        result = self.analyze(context={"quality": {"contiguous": False}})
        self.assertEqual(result["status"], "unknown")

    def test_flat_noise_like_input_is_not_a_global_gate(self):
        amfm = self.model("amfm")
        generic = self.model("wideband", classes=("OFDM", "CSS"))
        rng = np.random.default_rng(19)
        noise_like = (rng.normal(size=4096) + 1j * rng.normal(size=4096)).astype(np.complex64)
        result = decision.analyze_signal(noise_like, 256000, models=[amfm, generic], policy={"budget_ms": 0})
        self.assertEqual(result["budget"]["models_run"], 2)
        predictions = {a["model_id"]: a for a in result["predictions"]}
        self.assertEqual(predictions["amfm"]["status"], "rejected")
        self.assertEqual(predictions["wideband"]["model_label"], "OFDM")
        self.assertEqual(result["conclusions"]["waveform_family"]["label"], "OFDM")
        self.assertEqual(result["conclusions"]["modulation"]["status"], "unknown")
        self.assertTrue(any(h["id"] == "noise_like_or_spread" for h in result["hypotheses"]))

    def test_conflicting_closed_set_models_abstain_instead_of_averaging(self):
        result = self.analyze(models=[self.model("am"), self.model("fm", winner=1)])
        mod = result["conclusions"]["modulation"]
        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(mod["label"])
        self.assertEqual({a["label"] for a in mod["candidates"]}, {"AM", "FM"})
        self.assertTrue(all(a["score"] is None for a in mod["candidates"]))

    def test_level_mapping_keeps_protocol_and_emitter_identity_separate(self):
        model = self.model("pocsag", classes=("POCSAG", "LoRa"))
        model["level"] = "emitter"  # A historical misclassification of label level.
        result = self.analyze(models=[model])
        self.assertEqual(result["predictions"][0]["model_label"], "POCSAG")
        self.assertEqual(result["predictions"][0]["label_level"], "protocol")
        self.assertEqual(result["conclusions"]["protocol"]["status"], "candidate")
        self.assertEqual(result["label"], "POCSAG")
        self.assertEqual(result["conclusions"]["emitter"]["status"], "unknown")
        self.assertEqual(result["conclusions"]["service"]["status"], "unknown")

    def test_missing_or_malformed_expert_does_not_stop_other_experts(self):
        good = self.model("good")
        malformed = self.model("malformed")
        # A real dimension failure must be isolated by the safe existing loader.
        np.savez(Path(malformed["model_path"]) / "weights.npz", weights=np.zeros((2, 999)),
                 bias=np.zeros(2), mean=np.zeros(10), scale=np.ones(10))
        result = self.analyze(models=[{"id": "missing", "model_path": str(Path(self.root.name) / "absent")}, malformed, good])
        routes = {a["model_id"]: a for a in result["routes"]}
        self.assertEqual(routes["missing"]["status"], "error")
        self.assertEqual(routes["malformed"]["status"], "error")
        self.assertEqual(result["conclusions"]["modulation"]["label"], "AM")

    def test_missing_torch_error_is_local_and_never_becomes_heuristic_fallback(self):
        a, b = self.model("torch_missing"), self.model("healthy")
        original = ml.classify
        def inference(*args, **kwargs):
            if kwargs["model_path"] == a["model_path"]:
                raise ImportError("PyTorch is not installed")
            return original(*args, **kwargs)
        with mock.patch.object(ml, "classify", side_effect=inference):
            result = self.analyze(models=[a, b])
        self.assertEqual(len(result["predictions"]), 1)
        self.assertEqual(result["routes"][0]["status"], "error")
        self.assertEqual(result["conclusions"]["modulation"]["label"], "AM")

    def test_unvalidated_models_and_short_model_context_skip_expensive_inference(self):
        a = self.model("unvalidated", evaluated=False)
        b = self.model("long", length=4096)
        with mock.patch.object(ml, "classify", side_effect=AssertionError("must not infer")):
            result = self.analyze(models=[a, b])
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(a["status"] == "skipped" for a in result["routes"]))

    def test_model_count_and_cooperative_wall_budget(self):
        a, b = self.model("a"), self.model("b")
        result = decision.analyze_signal(self.iq, 256000, models=[a, b], policy={"budget_ms": 0, "max_models": 1})
        self.assertEqual(result["budget"]["models_run"], 1)
        self.assertEqual(sum(a["status"] == "skipped" for a in result["routes"]), 1)
        # Deterministic advancing clock: preprocessing exhausts the small budget.
        tick = iter(np.arange(0, 100, .01))
        with mock.patch.object(decision.time, "perf_counter", side_effect=lambda: float(next(tick))), \
             mock.patch.object(ml, "classify", side_effect=AssertionError("budget must skip inference")):
            result = decision.analyze_signal(self.iq, 256000, models=[a], policy={"budget_ms": 1})
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["budget"]["models_run"], 0)

    def test_hard_sample_cap_and_policy_validation(self):
        result = decision.analyze_signal(np.resize(self.iq, 70000), 256000)
        self.assertEqual(result["input"]["analyzed_samples"], 65536)
        self.assertTrue(result["input"]["truncated"])
        for invalid in ({"max_models": 9}, {"max_samples": 65537}, {"budget_ms": float("nan")}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                self.analyze(policy=invalid)
        with self.assertRaises(ValueError):
            self.analyze(models=[{"id": i} for i in range(66)])

    def test_decoder_evidence_requires_actual_window_and_passed_checks(self):
        evidence = {"level": "emitter", "label": "POCSAG", "decoder": "test-parser",
                    "validated": True, "checks_passed": True, "validation_method": "frame checks",
                    "sample_sha256": "wrong-window"}
        result = self.analyze(context={"decoder_evidence": [evidence]})
        self.assertEqual(result["conclusions"]["protocol"]["status"], "unknown")
        evidence["sample_sha256"] = hashlib.sha256(self.iq.tobytes()).hexdigest()
        result = self.analyze(context={"decoder_evidence": [evidence]})
        self.assertEqual(result["conclusions"]["protocol"]["status"], "observed")
        self.assertEqual(result["conclusions"]["emitter"]["status"], "unknown")
        json.dumps(result, allow_nan=False)

    def test_decoder_disagreement_cannot_be_overwritten_by_a_third_claim(self):
        common = {"level": "protocol", "decoder": "test-parser", "validated": True,
                  "checks_passed": True, "validation_method": "frame checks",
                  "sample_sha256": hashlib.sha256(self.iq.tobytes()).hexdigest()}
        claims = [{**common, "label": label} for label in ["POCSAG", "LoRa", "LoRa"]]
        result = self.analyze(context={"decoder_evidence": claims})
        self.assertEqual(result["conclusions"]["protocol"]["status"], "conflict")
        self.assertIsNone(result["conclusions"]["protocol"]["label"])

    def test_finite_large_numeric_inputs_remain_json_serializable(self):
        x = np.full(512, 3e38 + 3e38j, dtype=np.complex64)
        result = decision.analyze_signal(x, np.float64(256000), center_frequency_hz=np.float32(100e6))
        json.dumps(result, allow_nan=False)

    def test_clipping_needs_explicit_source_evidence(self):
        result = self.analyze(context={"quality": {"clipping_fraction": .5}})
        self.assertEqual(result["stages"][0]["status"], "rejected")
        self.assertFalse(result["features"])
        # IQ amplitude alone does not establish converter full scale or clipping.
        result = decision.analyze_signal(self.iq * 4, 256000)
        self.assertEqual(result["stages"][0]["status"], "passed")

    def test_registry_of_65_profiles_only_reads_metadata_for_8_inference_calls(self):
        original = self.model("shared")
        models = [{**original, "id": f"profile-{i}"} for i in range(65)]
        with mock.patch.object(ml, "load_model_metadata", wraps=ml.load_model_metadata) as metadata, \
             mock.patch.object(decision, "_model_hash", wraps=decision._model_hash) as artifact_hash, \
             mock.patch.object(ml, "classify", wraps=ml.classify) as classify:
            result = self.analyze(models=models)
        self.assertEqual(len(result["routes"]), 65)
        self.assertEqual(classify.call_count, 8)
        self.assertEqual(artifact_hash.call_count, 8)
        # classify itself revalidates its selected manifest; no calls concern
        # the57 skipped profiles, which share this same test artifact.
        self.assertLessEqual(metadata.call_count, 32)
        self.assertEqual(sum(a.get("skip_reason") == "model_budget" for a in result["routes"]), 57)
        self.assertTrue(all(a["domain"] is None for a in result["routes"][8:]))
        with mock.patch.object(ml, "load_model_metadata") as metadata, \
             mock.patch.object(decision, "_model_hash") as artifact_hash, \
             mock.patch.object(ml, "classify") as classify:
            stopped = self.analyze(models=models, policy={"budget_ms": 0, "max_models": 0})
        metadata.assert_not_called()
        artifact_hash.assert_not_called()
        classify.assert_not_called()
        self.assertEqual(sum(a.get("skip_reason") == "model_budget" for a in stopped["routes"]), 65)

    def test_waveform_and_modulation_profile_scopes_are_both_prioritized(self):
        model = self.model("model")
        models = [{**model, "id": "fallback", "families": ["chirp"]},
                  {**model, "id": "match", "families": ["single_carrier", "AM"]}]
        result = self.analyze(models=models, policy={"budget_ms": 0, "max_models": 1})
        self.assertEqual(result["predictions"][0]["model_id"], "match")
        self.assertIn("single_carrier", result["routes"][1]["matched_families"])
        self.assertIn("AM", result["routes"][1]["matched_families"])

    def test_optional_attribution_is_explicit_and_charged_to_expert_call(self):
        model = self.model("explain")
        off = self.analyze(models=[model])
        self.assertIsNone(off["predictions"][0]["explanation"])
        on = self.analyze(models=[model], policy={"budget_ms": 0, "explain_models": True})
        self.assertEqual(on["predictions"][0]["explanation"]["method"], "LDA logit-margin decomposition")
        self.assertEqual(on["budget"]["models_run"], 1)

    def test_mixed_custom_emitter_label_retains_its_original_level(self):
        model = self.model("mixed", classes=("POCSAG", "Emitter-123"), winner=1)
        model["level"] = "emitter"
        result = self.analyze(models=[model])
        self.assertEqual(result["predictions"][0]["label_level"], "emitter")
        self.assertEqual(result["conclusions"]["emitter"]["status"], "candidate")
        self.assertEqual(result["conclusions"]["protocol"]["status"], "unknown")

    def test_decoder_abstentions_rejected_and_semantic_aliases_do_not_conflict(self):
        common = {"level": "modulation", "decoder": "test-parser", "validated": True,
                  "checks_passed": True, "validation_method": "frame checks",
                  "sample_sha256": hashlib.sha256(self.iq.tobytes()).hexdigest()}
        result = self.analyze(context={"decoder_evidence": [{**common, "label": "  "}, {**common, "label": "unbekannt"}]})
        self.assertEqual(result["status"], "unknown")
        result = self.analyze(context={"decoder_evidence": [{**common, "label": "WFM"}, {**common, "label": "fm"}]})
        self.assertEqual(result["conclusions"]["modulation"]["status"], "observed")
        self.assertEqual(result["label"], "FM")

    def test_filter_transition_and_parent_edge_warn_without_label_voting(self):
        model = self.model("edge")
        plain = self.analyze(models=[model])
        edge = self.analyze(models=[model], context={"quality": {"filter_settled": False},
                           "parent_sample_rate": 2e6, "parent_center_frequency_hz": 100e6,
                           "detection": {"frequency_min_hz": 99e6, "frequency_max_hz": 99.1e6}})
        self.assertEqual(edge["conclusions"], plain["conclusions"])
        self.assertEqual(len(edge["warnings"]), 2)

    def test_experts_use_newest_tail_by_default_and_start_alignment_is_explicit(self):
        model = self.model("alignment")
        original = ml.classify
        seen = []
        def inference(iq, *args, **kwargs):
            seen.append(np.array(iq))
            return original(iq, *args, **kwargs)
        with mock.patch.object(ml, "classify", side_effect=inference):
            end = self.analyze(models=[model])
            start = self.analyze(models=[model], policy={"budget_ms": 0, "expert_alignment": "start"})
        np.testing.assert_array_equal(seen[0], self.iq[-1024:])
        np.testing.assert_array_equal(seen[1], self.iq[:1024])
        self.assertEqual(end["predictions"][0]["window"]["sample_start"], 1024)
        self.assertEqual(end["predictions"][0]["window"]["sample_stop"], 2048)
        self.assertEqual(start["predictions"][0]["window"]["sample_start"], 0)
        self.assertEqual(end["predictions"][0]["window"]["sample_sha256"], hashlib.sha256(self.iq[-1024:].tobytes()).hexdigest())

    def test_input_cap_uses_tail_and_records_original_window_offset(self):
        iq = np.resize(self.iq, 70000)
        tail = decision.analyze_signal(iq, 256000)
        head = decision.analyze_signal(iq, 256000, policy={"expert_alignment": "start"})
        self.assertEqual(tail["input"]["window_start"], 4464)
        self.assertEqual(tail["input"]["window_stop"], 70000)
        self.assertEqual(tail["input"]["sample_sha256"], hashlib.sha256(iq[-65536:].tobytes()).hexdigest())
        self.assertEqual(head["input"]["window_start"], 0)
        self.assertEqual(head["input"]["sample_sha256"], hashlib.sha256(iq[:65536].tobytes()).hexdigest())
        with self.assertRaises(ValueError):
            decision.normalize_policy({"expert_alignment": "middle"})

    def test_ofdm_and_qam_are_compatible_on_separate_label_axes(self):
        waveform = self.model("waveform", classes=("OFDM", "CSS"))
        modulation = self.model("subcarriers", classes=("QAM", "PSK"))
        # Known labels correct a historical modulation-level OFDM classifier.
        waveform["level"] = "modulation"
        result = self.analyze(models=[waveform, modulation])
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["conclusions"]["waveform_family"]["label"], "OFDM")
        self.assertEqual(result["conclusions"]["modulation"]["label"], "QAM")
        self.assertEqual(result["predictions"][0]["model_label"], "OFDM")
        self.assertEqual(result["predictions"][0]["label_level"], "waveform_family")
        self.assertTrue(result["conclusions"]["waveform_family"]["feature_hypotheses"])
        self.assertTrue(result["conclusions"]["waveform_family"]["feature_evidence_ids"])
        taxonomy = decision.get_taxonomy()
        self.assertIn("OFDM", {a["id"] for a in taxonomy["waveform_family"]})
        self.assertNotIn("OFDM", {a["id"] for a in taxonomy["modulation"]})
        self.assertEqual(len(decision.LEVELS), len(set(decision.LEVELS)))

    def test_css_waveform_cannot_imply_lora_protocol(self):
        model = self.model("css", classes=("CSS", "OFDM"))
        model["level"] = "waveform_family"
        result = self.analyze(models=[model])
        self.assertEqual(result["conclusions"]["waveform_family"]["label"], "CSS")
        self.assertEqual(result["conclusions"]["protocol"]["status"], "unknown")
        self.assertEqual(result["conclusions"]["service"]["status"], "unknown")
        self.assertEqual(result["conclusions"]["emitter"]["status"], "unknown")

    def test_checked_waveform_evidence_cannot_imply_a_protocol(self):
        evidence = {"level": "modulation", "label": "CSS", "decoder": "checked-waveform-parser",
                    "validated": True, "checks_passed": True, "validation_method": "known frame checks",
                    "sample_sha256": hashlib.sha256(self.iq.tobytes()).hexdigest()}
        result = self.analyze(context={"decoder_evidence": [evidence]})
        self.assertEqual(result["conclusions"]["waveform_family"]["status"], "observed")
        self.assertEqual(result["conclusions"]["waveform_family"]["label"], "CSS")
        self.assertTrue(result["conclusions"]["waveform_family"]["feature_hypotheses"])
        self.assertEqual(result["conclusions"]["protocol"]["status"], "unknown")

    def test_waveform_parent_and_child_are_compatible_and_all_evidence_remains(self):
        generic = self.model("generic", classes=("multicarrier", "single_carrier"))
        generic["level"] = "modulation"  # Known morphology corrects this level.
        specific = self.model("ofdm", classes=("OFDM", "CSS"))
        qam = self.model("qam", classes=("QAM", "PSK"))
        result = self.analyze(models=[generic, specific, qam])
        wave = result["conclusions"]["waveform_family"]
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(wave["status"], "candidate")
        self.assertEqual(wave["label"], "OFDM")
        self.assertEqual({a["label"] for a in wave["candidates"]}, {"multicarrier", "OFDM"})
        self.assertEqual(set(wave["model_ids"]), {"generic", "ofdm"})
        self.assertEqual(set(wave["evidence_ids"]), {"model:generic", "model:ofdm"})
        self.assertTrue(wave["feature_hypotheses"])
        self.assertEqual(result["conclusions"]["modulation"]["label"], "QAM")

    def test_unrelated_waveform_models_still_conflict(self):
        ofdm = self.model("ofdm", classes=("OFDM", "CSS"))
        css = self.model("css", classes=("CSS", "OFDM"))
        result = self.analyze(models=[ofdm, css])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conclusions"]["waveform_family"]["status"], "conflict")
        self.assertIsNone(result["conclusions"]["waveform_family"]["label"])
        self.assertEqual(result["conclusions"]["protocol"]["status"], "unknown")

    def test_missing_registry_profile_never_loads_current_directory_manifest(self):
        models = [{"id": "none", "model_path": None}, {"id": "empty", "model_path": ""},
                  {"id": "spaces", "model_path": "  "},
                  {"id": "deleted", "model_path": self.root.name,
                   "unavailable_reason": "Modell fehlt in der Datenbank"}]
        with mock.patch.object(ml, "load_model_metadata") as metadata, \
             mock.patch.object(decision, "_model_hash") as artifact_hash:
            result = self.analyze(models=models)
        metadata.assert_not_called()
        artifact_hash.assert_not_called()
        self.assertTrue(all(a["status"] == "error" and a["why"] for a in result["routes"]))
        self.assertEqual(result["budget"]["models_run"], 0)


if __name__ == "__main__":
    unittest.main()
