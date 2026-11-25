"""Campaign orchestration with numeric waveforms and fake hardware only."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from signalclassifier import generation, waveforms
from signalclassifier.db import Store
from signalclassifier.sigmf import recording_info, read_recording


class FakeTx:
    instances = []

    def __init__(self, config):
        self.config = dict(config)
        self.events = []
        self.state = {"tx_active": False, "off_acknowledged": True, "state": "new", "actual_config": dict(config)}
        self.instances.append(self)

    def open(self):
        self.events.append("open_muted")
        self.state["state"] = "muted"
        return self.status()

    def configure(self, config):
        if self.state["tx_active"]:
            raise AssertionError("configuration while TX active")
        self.config = dict(config)
        self.state["actual_config"] = dict(config)
        self.events.append("configure_muted")
        return self.status()

    def start_cyclic(self, iq):
        assert len(iq) and np.isfinite(iq).all() and np.max(np.abs(iq)) <= .800001
        self.events.append("start")
        self.iq = np.asarray(iq)
        self.state.update(tx_active=True, off_acknowledged=False, state="transmitting")
        return self.status()

    def stop(self, reason="stop"):
        self.events.append("stop")
        self.state.update(tx_active=False, off_acknowledged=True, state="muted")
        return self.status()

    def close(self):
        self.stop("close")
        self.events.append("close")
        self.state["state"] = "closed"

    def status(self):
        return copy.deepcopy(self.state)


class FakeRx:
    def __init__(self, config, tx):
        assert tx.state["tx_active"] is True
        self.sample_rate = config["sample_rate"]
        self.center_frequency = config["center_frequency_hz"]
        self.position = 0
        self.closed = False
        self.tx = tx

    def read(self, count):
        if self.closed:
            raise AssertionError("read after close")
        # The discarded prefix is independently distinguishable from the data.
        indices = self.position+np.arange(count)
        self.position += count
        return np.where(indices < int(self.sample_rate*.05), .01, .2).astype(np.complex64)

    def close(self):
        self.tx.events.append("rx_close")
        self.closed = True


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "db.sqlite3")
        self.manager = generation.GenerationManager(self.root, self.store, transmitter_factory=FakeTx, receiver_factory=FakeRx)
        self.addCleanup(self.manager.close)
        FakeTx.instances.clear()

    def config(self, **changes):
        return {"mode_ids": ["am"], "variants_per_mode": 1, "payload_repetitions": 3,
                "duration_seconds": .5, "settle_seconds": .05, "rx_preroll_seconds": .05,
                "transmitter": {"attenuation_offsets_db": [0]},
                "receiver": {"source": "rsp1a", "sample_rate": 520834}, **changes}

    def finish(self):
        self.manager._thread.join(timeout=10)
        self.assertFalse(self.manager._thread.is_alive())
        return self.manager.status()

    def start(self, config):
        plan = self.manager.plan(config)
        self.assertTrue(plan["ready"], plan["errors"])
        state = self.manager.start({"plan_id": plan["id"], "plan_sha256": plan["sha256"],
                                    "transmit_enabled": True, "setup_confirmed": True})
        return plan, state

    def test_planning_all_native_modes_is_deterministic_and_never_opens_a_device(self):
        config = {"mode_ids": [entry["id"] for entry in waveforms.catalogue()]}
        first, second = self.manager.plan(config), self.manager.plan(config)
        self.assertTrue(first["ready"], first["errors"])
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["cases"], second["cases"])
        self.assertEqual(len(first["cases"]), 270)
        self.assertEqual(FakeTx.instances, [])
        self.assertFalse(self.manager.status()["tx_active"])

    def test_power_variants_only_subtract_from_manual_ceiling_and_cover_explicit_values(self):
        plan = self.manager.plan(self.config(variants_per_mode=1, transmitter={"tx_gain_db": -40, "attenuation_offsets_db": [0, 3, 12]}))
        self.assertTrue(plan["ready"], plan["errors"])
        self.assertEqual(plan["config"]["variants_per_mode"], 3)
        self.assertEqual({case["tx_config"]["tx_gain_db"] for case in plan["cases"]}, {-40, -43, -52})
        for bad in ([-1], [50], [0.1]):
            with self.assertRaises(ValueError):
                self.manager.plan(self.config(transmitter={"tx_gain_db": -50, "attenuation_offsets_db": bad}))

    def test_frames_facets_and_labels_are_resolved_after_parameter_variation(self):
        plan = self.manager.plan(self.config(mode_ids=["ofdm"], label_facet="modulation", variants_per_mode=2,
            parameter_overrides={"ofdm": {"subcarrier_modulation": ["QPSK", "64QAM"]}}, receiver={"source": "rsp1a", "sample_rate": 2e6}))
        self.assertTrue(plan["ready"], plan["errors"])
        self.assertEqual(set(plan["summary"]["classes"]), {"QPSK", "64QAM"})
        for case in plan["cases"]:
            self.assertEqual(case["label"], case["waveform_spec"]["parameters"]["subcarrier_modulation"])

    def test_stale_or_unconfirmed_start_cannot_open_or_transmit(self):
        plan = self.manager.plan(self.config())
        for changes in ({"plan_sha256": "other"}, {"transmit_enabled": False}, {"setup_confirmed": False}, {"transmit_enabled": "true"}):
            payload = {"plan_id": plan["id"], "plan_sha256": plan["sha256"], "transmit_enabled": True, "setup_confirmed": True, **changes}
            with self.assertRaises(ValueError):
                self.manager.start(payload)
        self.assertEqual(FakeTx.instances, [])

    def test_exact_capture_preroll_labels_manifest_and_shutdown(self):
        plan, state = self.start(self.config())
        result = self.finish()
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["completed_cases"], 3)
        self.assertEqual(result["verified_recordings_count"], 3)
        self.assertFalse(result["busy"])
        self.assertFalse(result["tx_active"])
        detail = self.manager.detail(state["id"])
        self.assertEqual(len(detail["records"]), 3)
        roots = set()
        for row in detail["records"]:
            path = self.root / "recordings" / (row["recording_id"]+".sigmf-meta")
            info = recording_info(path)
            self.assertEqual(info["sample_count"], 260417)
            iq, metadata = read_recording(path, count=100)
            np.testing.assert_allclose(iq, .2)
            glob = metadata["global"]
            self.assertTrue(glob["signalclassifier:label_verified"])
            self.assertEqual(glob["signalclassifier:group_id"], state["id"])
            self.assertFalse(glob["sc:generation"]["rf_origin_verified"])
            self.assertEqual(glob["sc:generation"]["reference_kind"], "controlled_generator_reference")
            roots.add(glob["sc:origin_recording_id"])
        self.assertEqual(len(roots), 3)
        self.assertEqual(self.store.count("label"), 3)
        tx = FakeTx.instances[0]
        self.assertEqual(tx.events.count("start"), 3)
        self.assertLess(tx.events.index("stop"), tx.events.index("rx_close"))
        for case, row in zip(plan["cases"], detail["records"]):
            self.assertEqual(case["payload_root_id"], row["generation"]["payload_root_id"])
            self.assertEqual(case["planned_split"], row["generation"]["planned_split"])

    def test_same_payload_variants_keep_one_original_and_partial_overflow_is_not_labelled(self):
        class OverflowRx(FakeRx):
            def read(self, count):
                if self.position:
                    raise RuntimeError("SoapySDR receiver overflow -4 fixture")
                return super().read(count)
        self.manager._rx_factory = OverflowRx
        _, state = self.start(self.config(payload_repetitions=1))
        result = self.finish()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["verified_recordings_count"], 0)
        self.assertFalse(result["tx_active"])
        self.assertEqual(self.store.count("label"), 0)
        row = self.manager.detail(state["id"])["records"][0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("overflow", row["error"])
        path = self.root / "recordings" / (row["recording_id"]+".sigmf-meta")
        self.assertFalse(recording_info(path)["metadata"]["global"]["signalclassifier:label_verified"])

    def test_cancellation_closes_receiver_and_mutes_before_releasing_busy(self):
        entered = threading.Event()
        class SlowRx(FakeRx):
            def read(self, count):
                entered.set()
                time.sleep(.03)
                return super().read(count)
        self.manager._rx_factory = SlowRx
        self.start(self.config())
        self.assertTrue(entered.wait(3))
        self.manager.stop()
        state = self.finish()
        self.assertEqual(state["status"], "cancelled")
        self.assertFalse(state["busy"])
        self.assertFalse(state["tx_active"])
        self.assertEqual(state["verified_recordings_count"], 0)

    def test_restart_does_not_resume_or_open_hardware_and_preserves_unknown_rf(self):
        self.store.put("generation", "a"*32, {"status": "running", "busy": True, "tx_active": True})
        resumed = generation.GenerationManager(self.root, self.store, transmitter_factory=FakeTx, receiver_factory=FakeRx)
        self.assertEqual(resumed.status()["status"], "interrupted")
        self.assertIsNone(resumed.status()["tx_active"])
        self.assertEqual(FakeTx.instances, [])

    def test_invalid_bounds_and_hardware_mismatches_fail_before_open(self):
        for config in (self.config(frequencies_hz=[100e6]), self.config(frequencies_hz=[3e9]),
                       self.config(transport="unknown"), self.config(payload_repetitions=True),
                       self.config(receiver={"source": "pluto", "uri": "ip:192.168.2.1"}),
                       self.config(mode_ids=["missing"]), self.config(max_bytes=1024*1024)):
            with self.subTest(config=config):
                try:
                    result = self.manager.plan(config)
                except ValueError:
                    continue
                self.assertFalse(result["ready"])
        self.assertEqual(FakeTx.instances, [])

    def test_controls_keep_constant_planned_origins_and_noise_only_cannot_be_labelled_am(self):
        plan = self.manager.plan(self.config(mode_ids=["cw", "chirp"]))
        self.assertTrue(plan["ready"], plan["errors"])
        for mode in ("cw", "chirp"):
            cases = [case for case in plan["cases"] if case["mode_id"] == mode]
            self.assertEqual({case["planned_split"] for case in cases}, {"train"})
            self.assertEqual(len({case["payload_root_id"] for case in cases}), 1)
        from signalclassifier import onair
        entry = next(row for row in onair.catalogue() if row["id"] == "onair:am")
        entry.update(available=True, provider="onair")
        rejected = generation.build_plan(self.config(mode_ids=["onair:am"], receiver={"source": "rsp1a", "sample_rate": 2e6},
            parameter_overrides={"onair:am": {"sopt1": [1]}}), entries=[entry])
        self.assertFalse(rejected["ready"])
        self.assertTrue(any("Rauschen" in error for error in rejected["errors"]))

    def test_stop_waits_for_inflight_start_and_cannot_acknowledge_off_before_it(self):
        entered, release = threading.Event(), threading.Event()
        class DelayedStart(FakeTx):
            def start_cyclic(self, iq):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test start deadline")
                return super().start_cyclic(iq)
        self.manager._tx_factory = DelayedStart
        self.start(self.config(payload_repetitions=1))
        self.assertTrue(entered.wait(3))
        returned = []
        stopper = threading.Thread(target=lambda: returned.append(self.manager.stop()))
        stopper.start()
        try:
            time.sleep(.05)
            self.assertFalse(returned, "No off acknowledgement while a prior start still owns the lifecycle lock")
        finally:
            release.set()
        stopper.join(timeout=3)
        self.assertEqual(len(returned), 1)
        self.assertFalse(returned[0]["tx_active"])
        state = self.finish()
        self.assertFalse(state["tx_active"])
        self.assertEqual(FakeTx.instances[0].events.count("start"), 1)

    def test_unresponsive_reader_keeps_device_lease_without_parallel_close(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        class HungRead(FakeRx):
            def read(self, count):
                entered.set()
                release.wait(5)
                return super().read(count)

            def close(self):
                if not release.is_set():
                    raise AssertionError("receiver closed while read still owns it")
                closed.set()
                super().close()
        self.manager._reader_join_seconds = .02
        self.manager._rx_factory = HungRead
        self.start(self.config(payload_repetitions=1))
        try:
            self.assertTrue(entered.wait(3))
            self.manager.stop()
            deadline = time.monotonic()+2
            while not self.manager.status().get("receiver_lease_unresolved") and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(self.manager.status()["busy"])
            self.assertTrue(self.manager.status().get("receiver_lease_unresolved"))
            self.assertFalse(closed.is_set())
            self.assertFalse(self.manager.status()["tx_active"])
        finally:
            release.set()
        self.finish()
        self.assertTrue(closed.is_set())

    def test_reader_error_mutes_even_while_disk_append_is_blocked(self):
        writing, release, fault = threading.Event(), threading.Event(), threading.Event()
        original_append = generation.SigMFWriter.append
        class FaultAfterWriter(FakeRx):
            def read(self, count):
                if self.position:
                    writing.wait(3)
                    fault.set()
                    raise RuntimeError("receiver overflow while disk blocked")
                return super().read(count)

        def blocked_append(writer, iq):
            writing.set()
            release.wait(5)
            return original_append(writer, iq)
        self.manager._rx_factory = FaultAfterWriter
        with patch.object(generation.SigMFWriter, "append", blocked_append):
            self.start(self.config(payload_repetitions=1))
            try:
                self.assertTrue(fault.wait(3))
                deadline = time.monotonic()+1
                while self.manager.status().get("tx_active") is not False and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(self.manager.status()["tx_active"], "RF mute must not depend on disk completion")
                self.assertTrue(self.manager.status()["busy"])
            finally:
                release.set()
            self.assertEqual(self.finish()["status"], "failed")

    def test_receiver_close_can_block_only_after_transmitter_is_muted(self):
        entered, release = threading.Event(), threading.Event()
        class SlowClose(FakeRx):
            def close(self):
                entered.set()
                release.wait(5)
                super().close()
        self.manager._rx_factory = SlowClose
        self.start(self.config(payload_repetitions=1))
        try:
            self.assertTrue(entered.wait(3))
            self.assertFalse(self.manager.status()["tx_active"])
            self.assertTrue(self.manager.status()["busy"])
        finally:
            release.set()
        self.assertEqual(self.finish()["status"], "completed")

    def test_zero_iq_is_not_confirmed_and_actual_rate_cannot_exceed_byte_budget(self):
        class ZeroRx(FakeRx):
            def read(self, count):
                self.position += count
                return np.zeros(count, np.complex64)
        self.manager._rx_factory = ZeroRx
        self.start(self.config(payload_repetitions=1))
        state = self.finish()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["verified_recordings_count"], 0)
        self.assertIn("Null-IQ", state["error"])
        class RoundedRateRx(FakeRx):
            def __init__(self, config, tx):
                super().__init__(config, tx)
                self.sample_rate *= 1.0005
        self.manager._rx_factory = RoundedRateRx
        budget = 3*260417*8
        self.start(self.config(max_bytes=budget))
        state = self.finish()
        self.assertEqual(state["status"], "failed")
        self.assertLessEqual(state["bytes_written"], budget)
        self.assertEqual(state["verified_recordings_count"], 2)
        self.assertIn("Bytebudget", state["error"])

    def test_unexpected_adapter_close_error_cannot_restore_a_stale_off_claim(self):
        class CloseFailure(FakeTx):
            def close(self):
                raise RuntimeError("fixture cleanup failed after cached off")
        self.manager._tx_factory = CloseFailure
        self.start(self.config(payload_repetitions=1))
        state = self.finish()
        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["tx_active"])
        self.assertIn("Cleanup", state["error"])

    def test_stop_during_adapter_retirement_cannot_spawn_worker_into_next_campaign(self):
        entered, release = threading.Event(), threading.Event()
        class DelayedClose(FakeTx):
            def close(self):
                entered.set()
                release.wait(4)
                super().close()
        self.manager._tx_factory = DelayedClose
        self.start(self.config(payload_repetitions=1))
        try:
            self.assertTrue(entered.wait(3))
            previous_mute = self.manager._mute_thread
            stopped = self.manager.stop()
            self.assertTrue(stopped["busy"])
            self.assertIs(self.manager._mute_thread, previous_mute)
            self.assertFalse(previous_mute.is_alive())
        finally:
            release.set()
        self.finish()
        self.manager._tx_factory = FakeTx
        self.start(self.config(payload_repetitions=1))
        self.assertEqual(self.finish()["status"], "completed")
        self.assertFalse(self.manager._mute_thread.is_alive())

    def test_orphaned_tx_reconnect_is_asynchronous_and_holds_a_busy_lease(self):
        entered, release = threading.Event(), threading.Event()
        class DelayedOpen(FakeTx):
            def open(self):
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("fixture open timeout")
                return super().open()
        self.store.put("generation", "b"*32, {"status": "running", "busy": True, "tx_active": True,
                                               "config": generation.normalize_config(self.config())})
        resumed = generation.GenerationManager(self.root, self.store, transmitter_factory=DelayedOpen, receiver_factory=FakeRx)
        resumed._stop_join_seconds = .02
        try:
            stopped = resumed.stop()
            self.assertTrue(entered.is_set())
            self.assertTrue(stopped["busy"])
            self.assertIsNone(stopped["tx_active"])
            self.assertFalse(release.is_set(), "Pending response must not wait for a blocked reconnect")
        finally:
            release.set()
            if resumed._mute_thread:
                resumed._mute_thread.join(timeout=3)
            resumed.close()
        self.assertFalse(resumed.status()["busy"])
        self.assertFalse(resumed.status()["tx_active"])
        self.assertEqual(resumed.status()["status"], "interrupted")
