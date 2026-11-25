import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from signalclassifier.txhardware import MAX_TX_SAMPLES, PlutoTxAdapter, validate_tx_config


class FakePluto:
    tracked = {"sample_rate", "tx_lo", "tx_rf_bandwidth", "tx_hardwaregain_chan0", "tx_enabled_channels",
               "tx_cyclic_buffer", "rx_enabled_channels", "rx_lo", "rx_rf_bandwidth", "gain_control_mode_chan0",
               "rx_hardwaregain_chan0", "rx_buffer_size"}

    def __init__(self):
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "failures", set())
        object.__setattr__(self, "powerdown", "0")
        object.__setattr__(self, "buffer_active", False)
        object.__setattr__(self, "tx_hardwaregain_chan0", -20.)
        object.__setattr__(self, "sample_rate", 2000000)
        object.__setattr__(self, "tx_lo", 433000000)
        object.__setattr__(self, "tx_rf_bandwidth", 1600000)
        object.__setattr__(self, "rx_data", [])
        object.__setattr__(self, "last_tx", None)
        object.__setattr__(self, "ignored_assignments", set())
        object.__setattr__(self, "_ctx", SimpleNamespace(set_timeout=lambda value: self.call("set_timeout", value)))
        object.__setattr__(self, "_ctrl", SimpleNamespace(find_channel=self.find_channel))

    def __setattr__(self, key, value):
        if key in self.tracked:
            self.call("set_" + key, value)
            if key in self.ignored_assignments:
                return
        object.__setattr__(self, key, value)

    def call(self, operation, value=None):
        self.calls.append((operation, value))
        if operation in self.failures or f"{operation}:{value}" in self.failures:
            raise OSError(f"simulated {operation} failure")

    def find_channel(self, name, output):
        self.call("find_channel", (name, output))
        if name != "altvoltage1" or output is not True:
            raise AssertionError("TX LO channel expected")
        owner = self

        class Powerdown:
            @property
            def value(self):
                owner.call("read_powerdown")
                return owner.powerdown

            @value.setter
            def value(self, value):
                owner.call("set_powerdown", value)
                owner.powerdown = value

        return SimpleNamespace(attrs={"powerdown": Powerdown()})

    def tx_destroy_buffer(self):
        self.call("tx_destroy_buffer")
        self.buffer_active = False

    def disable_dds(self):
        self.call("disable_dds")

    def tx(self, data):
        if self.powerdown != "1" or self.tx_hardwaregain_chan0 != -89.75:
            raise AssertionError("upload must happen with LO powered down and maximum attenuation")
        self.buffer_active = True
        self.call("tx", len(data))
        self.last_tx = data.copy()

    def rx_destroy_buffer(self):
        self.call("rx_destroy_buffer")

    def rx(self):
        self.call("rx")
        return self.rx_data.pop(0)


class PlutoTxTests(unittest.TestCase):
    def setUp(self):
        self.device = FakePluto()
        self.factory = unittest.mock.Mock(return_value=self.device)
        self.loader = patch("signalclassifier.txhardware.importlib.import_module", return_value=SimpleNamespace(Pluto=self.factory))
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.adapter = PlutoTxAdapter({"uri": "usb:1.2.3", "center_frequency_hz": 433e6, "sample_rate": 1e6})
        self.addCleanup(self.adapter.close)

    def test_constructor_and_status_are_hardware_free_and_default_gain_is_attenuation(self):
        self.factory.assert_not_called()
        self.assertEqual(self.device.calls, [])
        initial = self.adapter.status()
        self.assertEqual(initial["state"], "new")
        self.assertIsNone(initial["tx_active"])
        self.assertFalse(initial["off_acknowledged"])
        self.assertEqual(initial["requested_config"]["tx_gain_db"], -50)
        initial["requested_config"]["tx_gain_db"] = 0
        self.assertEqual(self.adapter.status()["requested_config"]["tx_gain_db"], -50)
        self.assertFalse(self.adapter.status()["rf_off_verified"])
        self.assertIn("host kill/disconnect", self.adapter.status()["shutdown_scope"])

    def test_open_is_muted_and_upload_occurs_before_explicit_lo_enable(self):
        opened = self.adapter.open()
        self.factory.assert_called_once_with(uri="usb:1.2.3")
        self.assertIn(("set_timeout", 1500), self.device.calls)
        self.assertNotIn("tx", [name for name, _ in self.device.calls])
        self.assertNotIn(("set_powerdown", "0"), self.device.calls)
        self.assertEqual(opened["state"], "muted")
        self.assertTrue(opened["off_acknowledged"])
        self.assertEqual(opened["actual_config"]["tx_gain_db"], -89.75)
        iq = np.full(16, .1 + .2j, np.complex64)
        active = self.adapter.start_cyclic(iq)
        np.testing.assert_array_equal(self.device.last_tx, iq * 2 ** 14)
        np.testing.assert_array_equal(iq, np.full(16, .1 + .2j, np.complex64))
        self.assertEqual(active["state"], "transmitting")
        self.assertTrue(active["tx_active"])
        self.assertFalse(active["off_acknowledged"])
        self.assertEqual(active["actual_config"]["tx_gain_db"], -50)
        self.assertEqual(active["cyclic_period_seconds"], 16 / 1e6)
        self.assertLessEqual(active["tx_submitted_monotonic_ns"], active["tx_enabled_monotonic_ns"])
        upload = self.device.calls.index(("tx", 16))
        enabled = self.device.calls.index(("set_powerdown", "0"))
        self.assertLess(upload, enabled)
        before = list(self.device.calls)
        with self.assertRaises(RuntimeError):
            self.adapter.configure({"center_frequency_hz": 434e6})
        self.assertEqual(self.device.calls, before)
        self.adapter.status()
        self.assertEqual(self.device.calls, before, "cached status must not query hardware")
        stopped = self.adapter.stop("test_done")
        self.assertTrue(stopped["off_acknowledged"])
        self.assertFalse(self.device.buffer_active)
        self.assertEqual(self.device.powerdown, "1")
        self.adapter.configure({"center_frequency_hz": 434e6})
        second = self.adapter.start_cyclic(iq)
        self.assertNotEqual(second["generation_id"], active["generation_id"])
        self.assertEqual(second["actual_config"]["center_frequency_hz"], 434e6)

    def test_validation_rejects_nonstock_settings_and_fractional_attenuation_steps(self):
        self.assertEqual(validate_tx_config({})["tx_gain_db"], -50)
        self.assertEqual(validate_tx_config({"tx_gain_db": -89.75})["tx_gain_db"], -89.75)
        for key, value in (("center_frequency_hz", 125e6), ("center_frequency_hz", 3.8e9 + 1),
                           ("center_frequency_hz", 433e6 + .5), ("sample_rate", 520833),
                           ("sample_rate", 61.44e6 + 1), ("sample_rate", float("nan")),
                           ("sample_rate", True), ("rf_bandwidth_hz", 20e6 + 1),
                           ("rf_bandwidth_hz", 199999), ("tx_gain_db", -90),
                           ("tx_gain_db", .25), ("tx_gain_db", -30.1), ("uri", "a\nb"), ("uri", None)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_tx_config({key: value})
        self.factory.assert_not_called()

    def test_open_cleans_existing_cyclic_state_and_reopen_never_reuses_transmitting_state(self):
        self.device.buffer_active = True
        self.device.powerdown = "0"
        opened = self.adapter.open()
        self.assertTrue(opened["off_acknowledged"])
        self.assertFalse(self.device.buffer_active)
        first_destroy = self.device.calls.index(("tx_destroy_buffer", None))
        first_configuration = self.device.calls.index(("set_sample_rate", 1000000))
        self.assertLess(first_destroy, first_configuration)
        self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        reopened = self.adapter.open()
        self.assertEqual(reopened["state"], "muted")
        self.assertFalse(reopened["tx_active"])
        self.assertTrue(reopened["off_acknowledged"])
        self.assertFalse(self.device.buffer_active)
        self.factory.assert_called_once()
        self.device.failures.add("read_powerdown")
        with self.assertRaisesRegex(RuntimeError, "erneuten Öffnen"):
            self.adapter.open()
        uncertain = self.adapter.status()
        self.assertEqual(uncertain["state"], "unknown")
        self.assertFalse(uncertain["off_acknowledged"])
        self.assertIsNone(uncertain["lo_powerdown_readback"])

    def test_invalid_iq_is_rejected_without_sdk_calls_and_long_period_is_bounded(self):
        self.adapter.open()
        for iq in (np.ones(16, np.complex64), np.full(16, complex(float("nan"), 0)),
                   np.zeros((2, 8), np.complex64), np.zeros(16, np.float32),
                   np.empty(0, np.complex64), np.empty(MAX_TX_SAMPLES + 1, np.complex64)):
            before = list(self.device.calls)
            with self.subTest(shape=iq.shape), self.assertRaises(ValueError):
                self.adapter.start_cyclic(iq)
            self.assertEqual(self.device.calls, before)
        # A full low-symbol-rate burst may exceed the earlier 262144-sample cap.
        iq = np.full(262148, .8 + 0j, np.complex64)
        status = self.adapter.start_cyclic(iq)
        self.assertEqual(status["tx_sample_count"], len(iq))
        self.assertEqual(status["max_samples"], 4194304)
        self.assertLessEqual(float(np.max(np.abs(self.device.last_tx))), .8000001 * 2 ** 14)

    def test_failed_upload_and_failed_configuration_attempt_all_mute_steps(self):
        self.adapter.open()
        self.device.failures.add("tx")
        with self.assertRaisesRegex(RuntimeError, "simulated tx failure"):
            self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        status = self.adapter.status()
        self.assertEqual(status["state"], "muted")
        self.assertTrue(status["off_acknowledged"])
        self.assertFalse(self.device.buffer_active)
        self.assertNotIn(("set_powerdown", "0"), self.device.calls)
        self.device.failures.clear()
        self.device.failures.add("set_tx_lo")
        with self.assertRaisesRegex(RuntimeError, "set_tx_lo"):
            self.adapter.configure({"center_frequency_hz": 435e6})
        self.assertTrue(self.adapter.status()["off_acknowledged"])

    def test_unconfirmed_requested_attenuation_never_enables_tx_lo(self):
        self.adapter.open()
        self.device.ignored_assignments.add("tx_hardwaregain_chan0")
        with self.assertRaisesRegex(RuntimeError, "TX-Dämpfung"):
            self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        self.assertNotIn(("set_powerdown", "0"), self.device.calls)
        self.assertTrue(self.adapter.status()["off_acknowledged"])
        self.assertFalse(self.device.buffer_active)

    def test_unaccepted_clock_or_out_of_range_readback_prevents_transmission(self):
        self.device.ignored_assignments.add("sample_rate")
        with self.assertRaisesRegex(RuntimeError, "TX-Sample-Rate"):
            self.adapter.open()
        self.assertEqual(self.adapter.status()["actual_config"]["sample_rate"], 2e6)
        self.assertNotIn("tx", [name for name, _ in self.device.calls])
        self.assertTrue(self.adapter.status()["off_acknowledged"])
        with self.assertRaisesRegex(RuntimeError, "TX-Sample-Rate"):
            self.adapter.open()
        self.adapter.close()
        self.device.ignored_assignments.add("tx_lo")
        self.device.ignored_assignments.remove("sample_rate")
        object.__setattr__(self.device, "tx_lo", float("nan"))
        with self.assertRaisesRegex(RuntimeError, "Standardgrenzen"):
            self.adapter.open()
        self.assertNotIn(("set_powerdown", "0"), self.device.calls)

    def test_cleanup_failures_do_not_skip_other_actions_or_claim_rf_off(self):
        self.adapter.open()
        self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        self.device.calls.clear()
        self.device.failures.update({"set_powerdown:1", "set_tx_hardwaregain_chan0", "tx_destroy_buffer", "disable_dds"})
        stopped = self.adapter.stop("disconnect")
        actions = [name for name, _ in self.device.calls]
        for expected in ("set_powerdown", "set_tx_hardwaregain_chan0", "tx_destroy_buffer", "disable_dds"):
            self.assertIn(expected, actions)
        self.assertEqual(stopped["state"], "unknown")
        self.assertIsNone(stopped["tx_active"])
        self.assertFalse(stopped["off_acknowledged"])
        self.assertGreaterEqual(len(stopped["cleanup_errors"]), 4)
        closed = self.adapter.close()
        self.assertEqual(closed["state"], "unknown")
        before = list(self.device.calls)
        self.adapter.close()
        self.assertEqual(self.device.calls, before)

    def test_open_failure_never_falls_back_to_simulation_and_still_tries_cleanup(self):
        self.device.failures.add("set_timeout")
        with self.assertRaisesRegex(RuntimeError, "set_timeout"):
            self.adapter.open()
        operations = [name for name, _ in self.device.calls]
        for expected in ("set_tx_hardwaregain_chan0", "tx_destroy_buffer", "disable_dds"):
            self.assertIn(expected, operations)
        self.assertNotIn("tx", operations)
        self.assertEqual(self.adapter.status()["state"], "unknown")
        self.assertFalse(self.adapter.status()["off_acknowledged"])

    def test_shared_receiver_uses_same_handle_clock_and_preserves_sample_continuity(self):
        self.adapter.open()
        self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        before = len(self.device.calls)
        with self.assertRaisesRegex(ValueError, "dieselbe"):
            self.adapter.shared_receiver({"sample_rate": 2e6})
        self.assertFalse(any(name.startswith("set_rx") for name, _ in self.device.calls[before:]))
        raw = (np.arange(32) + 2j * np.arange(32)).astype(np.complex64)
        self.device.rx_data = [raw[:16], [raw[16:]]]
        rx = self.adapter.shared_receiver({"sample_rate": 1e6, "center_frequency_hz": 434e6, "block_size": 16})
        self.factory.assert_called_once()
        self.assertFalse(any(name == "set_sample_rate" for name, _ in self.device.calls[before:]))
        self.assertEqual((rx.sample_rate, rx.center_frequency, rx.paced), (1e6, 434e6, False))
        blocks = [rx.read(5), rx.read(11), rx.read(16)]
        np.testing.assert_array_equal(np.concatenate(blocks), raw / 2048)
        self.assertEqual(sum(name == "rx" for name, _ in self.device.calls), 2)
        self.assertTrue(self.adapter.status()["tx_active"])
        rx.close()
        self.assertFalse(self.adapter.status()["rx_open"])
        self.assertTrue(self.adapter.status()["tx_active"], "RX close must not stop its borrowed TX handle")
        self.assertTrue(self.device.buffer_active)
        with self.assertRaises(RuntimeError):
            rx.read(16)

    def test_shared_receiver_failure_stops_tx_and_shared_clock_changes_require_rx_close(self):
        self.adapter.open()
        self.adapter.start_cyclic(np.full(16, .2j, np.complex64))
        rx = self.adapter.shared_receiver({"block_size": 16})
        self.device.rx_data = [np.full(16, complex(float("nan"), 0))]
        with self.assertRaisesRegex(RuntimeError, "RX-IQ"):
            rx.read(16)
        self.assertTrue(self.adapter.status()["off_acknowledged"])
        with self.assertRaisesRegex(RuntimeError, "RX schließen"):
            self.adapter.configure({"sample_rate": 2e6})
        rx.close()
        self.assertEqual(self.adapter.configure({"sample_rate": 2e6})["actual_config"]["sample_rate"], 2e6)

    def test_readback_failure_and_rx_cleanup_failure_remain_visible(self):
        self.adapter.open()
        rx = self.adapter.shared_receiver({"block_size": 16})
        self.device.failures.add("rx_destroy_buffer")
        result = self.adapter.close()
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["connected"])
        self.assertFalse(result["off_acknowledged"])
        self.assertTrue(any(item["action"] == "rx_destroy_buffer" for item in result["cleanup_errors"]))
        json.dumps(result, allow_nan=False)
        with self.assertRaises(RuntimeError):
            rx.read(16)


if __name__ == "__main__":
    unittest.main()
