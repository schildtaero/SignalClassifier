import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from signalclassifier import onair


FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, pathlib, struct, sys, time
args = sys.argv[1:]
if os.environ.get("FAKE_ONAIR_ARGV"):
    pathlib.Path(os.environ["FAKE_ONAIR_ARGV"]).write_text(json.dumps(args))
if os.environ.get("FAKE_ONAIR_PID"):
    pathlib.Path(os.environ["FAKE_ONAIR_PID"]).write_text(str(os.getpid()))
behavior = os.environ.get("FAKE_ONAIR_BEHAVIOR", "ok")
if behavior == "sleep":
    time.sleep(10)
if behavior == "exit":
    sys.exit(7)
mode = args[args.index("--standard") + 1]
output = pathlib.Path(args[args.index("--record") + 1])
description = {"pocsag": "pager", "flex": "pager", "am": "general",
               "dvbt": "dvb", "t2": "dvb"}.get(mode, mode)
rate = {"fm": 4000000, "atsc": 8000000,
        "dvbt": 64000000 / 7, "t2": 64000000 / 7}.get(mode, 2000000)
count = 32
if behavior == "oversize":
    with output.open("wb") as stream:
        stream.truncate(128 * 1024 * 1024 + 8)
    time.sleep(10)
value = float("nan") if behavior == "nan_iq" else 0.0 if behavior == "silence" else .25
output.write_bytes(struct.pack("<ff", value, 0.0) * count)
metadata = {"global": {"core:datatype": "cf32_le", "core:sample_rate": rate,
    "core:dataset": output.name, "core:description": description,
    "core:sample_count": count}, "captures": [{"core:sample_start": 0}], "annotations": []}
if behavior == "bad_rate": metadata["global"]["core:sample_rate"] = rate + 1000
if behavior == "nan_rate": metadata["global"]["core:sample_rate"] = float("nan")
if behavior == "bad_family": metadata["global"]["core:description"] = "dvb"
if behavior == "bad_count": metadata["global"]["core:sample_count"] += 1
if behavior == "bad_dataset": metadata["global"]["core:dataset"] = "/outside/private.cf32"
if behavior == "bad_dtype": metadata["global"]["core:datatype"] = "ci8_le"
if behavior != "no_metadata":
    output.with_suffix(".sigmf-meta").write_text(json.dumps(metadata))
if behavior == "missing_generator": print("no built-in test signal for this mode yet: the source sends silence")
if behavior == "logs": print("x" * 200000)
print("fake export complete")
'''


class OnAirTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "fake-onair-cli"
        self.binary.write_text(FAKE_CLI)
        self.binary.chmod(0o755)
        self.work = self.root / "work"
        self.arguments = self.root / "arguments.json"
        self.pid = self.root / "pid"
        env = {"SIGNALCLASSIFIER_ONAIR_CLI": str(self.binary),
               "FAKE_ONAIR_ARGV": str(self.arguments), "FAKE_ONAIR_PID": str(self.pid),
               "FAKE_ONAIR_BEHAVIOR": "ok"}
        self.environment = mock.patch.dict(os.environ, env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def generate(self, mode="pocsag", **spec):
        return onair.generate({"mode_id": "onair:" + mode, **spec}, workdir=self.work)

    def assert_process_stopped(self):
        if self.pid.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(self.pid.read_text()), 0)

    def test_catalogue_is_pure_closed_factual_registry_and_not_marketing_count(self):
        with mock.patch.object(onair.subprocess, "Popen", side_effect=AssertionError("catalogue must not launch")):
            data = onair.capabilities()
        rows = data["generators"]
        self.assertEqual(len(rows), 301)
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        self.assertNotIn("onair:iptv", {row["id"] for row in rows})
        self.assertFalse(data["executable_version_verified"])
        self.assertFalse(data["native_seed_control"])
        self.assertEqual(data["source_commit"], onair.SOURCE_COMMIT)
        for row in rows:
            self.assertGreater(row["sample_rate"], 0)
            self.assertEqual(set(row["facets"]), {"waveform_family", "modulation", "protocol", "service", "emitter"})
            self.assertEqual(row["implementation_kind"], "external_fixture")
        rows[0]["parameters"].clear()
        self.assertTrue(onair.catalogue()[0]["parameters"])
        with mock.patch.object(onair, "_binary", return_value=None):
            self.assertTrue(all(not row["available"] for row in onair.catalogue()))

    def test_explicit_prefix_preserves_native_rate_ancestry_and_does_not_claim_seed_control(self):
        value = self.generate(sample_count=8, seed=42, parameters={"sopt2": 2, "sopt3": 7})
        np.testing.assert_array_equal(value["iq"], np.full(8, .25, dtype=np.complex64))
        self.assertEqual(value["sample_rate"], 2e6)
        provenance = value["provenance"]
        self.assertTrue(provenance["truncated"])
        self.assertEqual(provenance["full_export_sample_count"], 32)
        self.assertEqual(provenance["returned_sample_count"], 8)
        self.assertFalse(provenance["native_seed_control"])
        self.assertEqual(provenance["requested_seed"], 42)
        self.assertEqual(provenance["payload_family_root"], "onair:pocsag")
        self.assertEqual(provenance["scope"], "fixture_segment_no_protocol_compliance_proof")
        args = json.loads(self.arguments.read_text())
        self.assertIn("--synthetic", args)
        self.assertNotIn("--rate", args)
        self.assertNotIn("--device", args)
        self.assertNotIn("--hackrf", args)
        self.assertNotIn("--seed", args)
        self.assertEqual(args[args.index("--freq") + 1], "0")
        self.assertEqual(list(self.work.iterdir()), [])

    def test_full_export_is_never_silently_cropped(self):
        self.assertFalse(self.generate()["provenance"]["truncated"])
        with mock.patch.object(onair, "MAX_RETURN_SAMPLES", 16):
            with self.assertRaisesRegex(onair.OnAirError, "expliziten sample_count"):
                self.generate()
            self.assertEqual(len(self.generate(sample_count=8)["iq"]), 8)
        with self.assertRaisesRegex(onair.OnAirError, "kürzer"):
            self.generate(sample_count=33)

    def test_unknown_modes_options_and_arbitrary_process_settings_refuse_before_spawn(self):
        bad = [{"mode_id": "onair:not-a-mode"}, {"mode_id": "onair:iptv"},
               {"mode_id": "onair:pocsag", "executable": "/bin/false"},
               {"mode_id": "onair:pocsag", "parameters": {"--hackrf": 1}},
               {"mode_id": "onair:pocsag", "parameters": {"sopt0": 2}},
               {"mode_id": "onair:pocsag", "parameters": {"sopt2": 7}},
               {"mode_id": "onair:pocsag", "parameters": {"sopt1": True}},
               {"mode_id": "onair:pocsag", "parameters": {"export_seconds": 6}},
               {"mode_id": "onair:pocsag", "parameters": {"snr_db": float("nan")}},
               {"mode_id": "onair:pocsag", "sample_count": 0},
               {"mode_id": "onair:pocsag", "sample_count": onair.MAX_RETURN_SAMPLES + 1},
               {"mode_id": "onair:pocsag", "sample_count": 2.0},
               {"mode_id": "onair:pocsag", "sample_rate": 1e6}]
        with mock.patch.object(onair, "_export", side_effect=AssertionError("invalid specification must not spawn")):
            for spec in bad:
                with self.subTest(spec=spec), self.assertRaises(onair.OnAirError):
                    onair.generate(spec, workdir=self.work)
        self.assertFalse(self.work.exists())

    def test_tv_selectors_are_explicit_because_standard_flag_alone_is_insufficient(self):
        for mode, flag in (("dvbt", "--dvbt"), ("atsc", "--atsc")):
            with self.subTest(mode=mode):
                self.generate(mode)
                self.assertIn(flag, json.loads(self.arguments.read_text()))

    def test_normalization_is_available_to_the_planner_without_a_process_or_workdir(self):
        with mock.patch.object(onair.subprocess, "Popen", side_effect=AssertionError("preflight must not spawn")):
            value = onair.normalize_spec({"mode_id": "onair:pocsag", "sample_count": 256,
                                          "parameters": {"sopt2": 2}})
            self.assertEqual(value["sample_rate"], 2e6)
            self.assertEqual(value["parameters"]["snr_db"], 100)
            self.assertEqual(value["parameters"]["sopt2"], 2)
            with self.assertRaises(onair.OnAirError):
                onair.normalize_spec({"mode_id": "onair:pocsag", "parameters": {"sopt2": 7}})
        self.assertFalse(self.work.exists())

    def test_mode_family_rate_count_format_and_dataset_are_verified(self):
        for behavior in ("bad_rate", "nan_rate", "bad_family", "bad_count", "bad_dataset", "bad_dtype", "no_metadata", "nan_iq", "silence", "missing_generator"):
            with self.subTest(behavior=behavior), mock.patch.dict(os.environ, {"FAKE_ONAIR_BEHAVIOR": behavior}):
                with self.assertRaises(onair.OnAirError):
                    self.generate()
                self.assertEqual(list(self.work.iterdir()), [])

    def test_general_options_update_modulation_and_noise_does_not_keep_protocol_label(self):
        value = self.generate("general", parameters={"sopt0": 2})
        self.assertEqual(value["facets"]["modulation"], "AM")
        self.assertEqual(self.generate("am")["facets"]["modulation"], "AM")
        value = self.generate("general", parameters={"sopt1": 1})
        self.assertIsNone(value["facets"]["modulation"])
        self.assertIsNone(value["facets"]["protocol"])

    def test_failed_process_and_large_output_are_stopped_and_removed(self):
        for behavior in ("exit", "oversize"):
            with self.subTest(behavior=behavior), mock.patch.dict(os.environ, {"FAKE_ONAIR_BEHAVIOR": behavior}):
                with self.assertRaises(onair.OnAirError):
                    self.generate()
                self.assert_process_stopped()
                self.assertEqual(list(self.work.iterdir()), [])

    def test_timeout_and_cancellation_terminate_the_process_group(self):
        with mock.patch.dict(os.environ, {"FAKE_ONAIR_BEHAVIOR": "sleep"}):
            with self.assertRaisesRegex(onair.OnAirError, "Zeitlimit"):
                onair.generate({"mode_id": "onair:pocsag"}, workdir=self.work, timeout=.15)
            self.assert_process_stopped()
            event = threading.Event()
            timer = threading.Timer(.15, event.set)
            timer.start()
            try:
                with self.assertRaisesRegex(onair.OnAirError, "abgebrochen"):
                    onair.generate({"mode_id": "onair:pocsag"}, workdir=self.work, stop_event=event, timeout=3)
            finally:
                timer.cancel(); timer.join()
            self.assert_process_stopped()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_pre_cancel_and_missing_binary_do_not_create_artifacts(self):
        event = threading.Event(); event.set()
        with self.assertRaisesRegex(onair.OnAirError, "abgebrochen"):
            onair.generate({"mode_id": "onair:pocsag"}, workdir=self.work, stop_event=event)
        with mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_ONAIR_CLI": str(self.root / "missing")}):
            with self.assertRaisesRegex(onair.OnAirError, "CLI fehlt"):
                self.generate()
        self.assertFalse(self.work.exists())

    def test_logs_are_drained_without_unbounded_storage(self):
        with mock.patch.dict(os.environ, {"FAKE_ONAIR_BEHAVIOR": "logs"}):
            value = self.generate()
        logs = value["provenance"]["log_tail"]
        self.assertLessEqual(len(logs.encode()), onair.MAX_LOG_BYTES)
        self.assertIn("fake export complete", logs)

    def test_managed_lookup_and_library_environment_do_not_adopt_custom_sibling_libraries(self):
        home = self.root / "home"
        base = home / ".local/share/signalclassifier/onair"
        portable = base / "4.6.0"
        binary = portable / "bin/onair-cli"
        binary.parent.mkdir(parents=True)
        binary.write_text(FAKE_CLI); binary.chmod(0o755)
        (portable / "lib/ffmpeg").mkdir(parents=True)
        (base / "current").symlink_to(portable, target_is_directory=True)
        with mock.patch.object(onair.Path, "home", return_value=home), mock.patch.dict(os.environ, {"SIGNALCLASSIFIER_ONAIR_CLI": "", "LD_LIBRARY_PATH": "/existing"}):
            self.assertEqual(onair._binary(), str(binary.resolve()))
            env = onair._process_environment(str(binary))
            self.assertEqual(env["LD_LIBRARY_PATH"].split(":"), [str(portable / "lib/ffmpeg"), str(portable / "lib"), "/existing"])
            self.assertEqual(onair._process_environment(str(self.binary))["LD_LIBRARY_PATH"], "/existing")
            # An external symlink must not smuggle an unrelated library folder in.
            (portable / "lib/ffmpeg").rmdir()
            outside = self.root / "outside"; outside.mkdir()
            (portable / "lib/ffmpeg").symlink_to(outside, target_is_directory=True)
            self.assertNotIn(str(outside), onair._process_environment(str(binary))["LD_LIBRARY_PATH"])


if __name__ == "__main__":
    unittest.main()
