"""Explicit Pluto TX for a controlled laboratory, with cached status.

No hardware is opened by importing this module or constructing an adapter.
The scale 2**14 follows the ADI pyadi-iio examples; it is a digital interface
scale, not a calibrated RF level or a claim about ADC/DAC resolution.

Sources:
https://analogdevicesinc.github.io/pyadi-iio/buffers/index.html
https://github.com/analogdevicesinc/pyadi-iio/blob/main/examples/pluto.py
https://wiki.analog.com/university/tools/pluto/hacking/listening_to_yourself

All SDK calls share one lock, including the optional RX view. A blocking RX
call has a 1500 ms context timeout. A killed/disconnected host cannot promise
physical RF shutdown of a previously submitted cyclic buffer.
"""
from __future__ import annotations

import copy
import importlib
import math
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone

import numpy as np


MAX_TX_SAMPLES = 4_194_304
MAX_RX_SAMPLES = 1_048_576
TX_DIGITAL_SCALE = 2 ** 14
TX_MAX_NORMALIZED_PEAK = 0.8
MIN_TX_GAIN_DB = -89.75


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _number(config, key, default, minimum, maximum, *, integer=False):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} muss eine Zahl sein.")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum or (integer and not value.is_integer()):
        suffix = " (ganze Hz/Samples)" if integer else ""
        raise ValueError(f"{key} muss zwischen {minimum:g} und {maximum:g} liegen{suffix}.")
    return int(value) if integer else value


def validate_tx_config(config: dict) -> dict:
    """Validate stock limits without importing pyadi-iio or opening a URI."""
    if not isinstance(config, dict):
        raise ValueError("TX-Konfiguration muss ein Objekt sein.")
    uri = config.get("uri", "ip:192.168.2.1")
    if (not isinstance(uri, str) or not uri.strip() or len(uri) > 512
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in uri)):
        raise ValueError("Pluto-URI muss 1–512 Zeichen ohne Steuerzeichen enthalten.")
    result = {"uri": uri,
              "center_frequency_hz": _number(config, "center_frequency_hz", 433_000_000, 325_000_000, 3_800_000_000, integer=True),
              "sample_rate": _number(config, "sample_rate", 2_000_000, 520_834, 61_440_000, integer=True),
              "tx_gain_db": _number(config, "tx_gain_db", -50, MIN_TX_GAIN_DB, 0)}
    result["rf_bandwidth_hz"] = _number(config, "rf_bandwidth_hz",
        min(20_000_000, max(200_000, int(result["sample_rate"] * .8))), 200_000, 20_000_000, integer=True)
    if not math.isclose(result["tx_gain_db"] * 4, round(result["tx_gain_db"] * 4), abs_tol=1e-8, rel_tol=0):
        raise ValueError("tx_gain_db muss einem 0,25-dB-Schritt entsprechen.")
    return result


class PlutoTxAdapter:
    """One explicit SDK handle, one TX buffer and at most one borrowed RX view."""

    def __init__(self, config: dict):
        self._config = validate_tx_config(config)
        self._lock = threading.RLock()
        self._device = None
        self._powerdown = None
        self._receiver = None
        self._status = {"state": "new", "connected": False, "tx_active": None,
            "off_acknowledged": False, "buffer_destroyed": None,
            "lo_powerdown_readback": None, "cleanup_errors": [], "error": None,
            "requested_config": dict(self._config), "actual_config": None,
            "generation_id": None, "rx_open": False, "rf_off_verified": False,
            "digital_scale": TX_DIGITAL_SCALE, "max_samples": MAX_TX_SAMPLES,
            "normalized_peak_limit": TX_MAX_NORMALIZED_PEAK,
            "shutdown_scope": "SDK acknowledgements only; physical RF not measured; host kill/disconnect is not an RF shutdown guarantee"}

    def status(self) -> dict:
        """Cached metadata only; never query the hardware from a UI poll."""
        with self._lock:
            return copy.deepcopy(self._status)

    def _read_powerdown(self):
        raw = self._powerdown.value
        if str(raw).strip() not in {"0", "1"}:
            raise RuntimeError("Pluto lieferte keinen gültigen TX-LO-Powerdown-Status.")
        return str(raw).strip() == "1"

    def _actual(self):
        fields = {"sample_rate": (self._device.sample_rate, 520834, 61.44e6),
                  "center_frequency_hz": (self._device.tx_lo, 325e6, 3.8e9),
                  "rf_bandwidth_hz": (self._device.tx_rf_bandwidth, 200e3, 20e6),
                  "tx_gain_db": (self._device.tx_hardwaregain_chan0, MIN_TX_GAIN_DB, 0)}
        actual = {}
        for key, (value, low, high) in fields.items():
            if isinstance(value, bool):
                raise RuntimeError(f"Ungültiger Pluto-Readback für {key}.")
            value = float(value)
            if not math.isfinite(value) or not low <= value <= high:
                raise RuntimeError(f"Pluto-Readback für {key} liegt außerhalb der Standardgrenzen.")
            actual[key] = value
        self._status["actual_config"] = actual
        self._status["readback_differences"] = {key: {"requested": self._config[key], "actual": actual[key]}
            for key in ("sample_rate", "center_frequency_hz", "rf_bandwidth_hz") if actual[key] != self._config[key]}
        return actual

    def _require_open(self):
        if self._device is None:
            raise RuntimeError("Pluto-TX ist nicht geöffnet.")

    def _confirm_clock(self, actual):
        if (abs(actual["sample_rate"] - self._config["sample_rate"]) > 1
                or actual["center_frequency_hz"] != self._config["center_frequency_hz"]):
            raise RuntimeError("Pluto hat die gewünschte TX-Sample-Rate oder Mittenfrequenz nicht eingestellt; Sendestart abgebrochen.")

    def _stop_locked(self, reason):
        self._status["stop_reason"] = str(reason)[:200]
        if self._device is None:
            return
        errors = []
        actions = (("lo_powerdown", lambda: setattr(self._powerdown, "value", "1")),
                   ("maximum_attenuation", lambda: setattr(self._device, "tx_hardwaregain_chan0", MIN_TX_GAIN_DB)),
                   ("tx_destroy_buffer", lambda: self._device.tx_destroy_buffer()),
                   ("disable_dds", lambda: self._device.disable_dds()))
        successes = {}
        for name, action in actions:
            try:
                action()
                successes[name] = True
            except Exception as exc:
                successes[name] = False
                errors.append({"action": name, "error": str(exc)[:500]})
        lo_off = None
        gain = None
        try:
            lo_off = self._read_powerdown()
        except Exception as exc:
            errors.append({"action": "lo_powerdown_readback", "error": str(exc)[:500]})
        try:
            gain = float(self._device.tx_hardwaregain_chan0)
            if not math.isfinite(gain):
                raise RuntimeError("Nicht endlicher Gain-Readback.")
        except Exception as exc:
            gain = None
            errors.append({"action": "attenuation_readback", "error": str(exc)[:500]})
        if lo_off is False:
            errors.append({"action": "lo_powerdown_verify", "error": "TX-LO-Powerdown wurde nicht bestätigt."})
        if gain is not None and gain != MIN_TX_GAIN_DB:
            errors.append({"action": "attenuation_verify", "error": "Maximale TX-Dämpfung wurde nicht bestätigt."})
        off = all(successes.values()) and lo_off is True and gain == MIN_TX_GAIN_DB and not errors
        if self._status["actual_config"] is not None:
            self._status["actual_config"]["tx_gain_db"] = gain
        self._status.update(state="muted" if off else "unknown", tx_active=False if off else None,
            off_acknowledged=off, buffer_destroyed=successes.get("tx_destroy_buffer", False),
            lo_powerdown_readback=lo_off, attenuation_readback_db=gain, cleanup_errors=errors,
            tx_stopped_at_utc=_utcnow(), tx_stopped_monotonic_ns=time.monotonic_ns())

    def open(self) -> dict:
        with self._lock:
            if self._device is not None:
                try:
                    self._stop_locked("reopen_muted")
                    if not self._status["off_acknowledged"]:
                        raise RuntimeError("Pluto konnte beim erneuten Öffnen nicht bestätigt stummgeschaltet werden.")
                    if self._receiver is None:
                        self._configure_locked(self._config)
                    else:
                        self._confirm_clock(self._actual())
                except Exception as exc:
                    self._stop_locked("reopen_failed")
                    self._status["error"] = str(exc)[:1000]
                    raise RuntimeError(str(exc)) from exc
                return self.status()
            self._status.update(state="opening", tx_active=None, off_acknowledged=False,
                                generation_id=None, actual_config=None, error=None, cleanup_errors=[])
            try:
                adi = importlib.import_module("adi")
                self._device = adi.Pluto(uri=self._config["uri"])
                self._status["connected"] = True
                self._device._ctx.set_timeout(1500)
                self._powerdown = self._device._ctrl.find_channel("altvoltage1", True).attrs["powerdown"]
                self._stop_locked("open_muted")
                if not self._status["off_acknowledged"]:
                    raise RuntimeError("Pluto konnte beim Öffnen nicht bestätigt stummgeschaltet werden.")
                self._configure_locked(self._config)
            except Exception as exc:
                self._stop_locked("open_failed")
                message = ("Pluto-Treiber fehlt: pyadi-iio und libiio installieren."
                           if isinstance(exc, ImportError) else str(exc))
                self._status.update(state="unknown", error=message[:1000])
                raise RuntimeError(message) from exc
            return self.status()

    def _configure_locked(self, config):
        self._require_open()
        if self._status["state"] != "muted" or not self._status["off_acknowledged"] or not self._read_powerdown():
            raise RuntimeError("TX-Konfiguration ist nur bei bestätigt stummgeschaltetem Pluto erlaubt.")
        if self._receiver is not None:
            raise RuntimeError("Gemeinsamen RX vor einer TX-/Sample-Rate-Änderung schließen.")
        self._device.sample_rate = config["sample_rate"]
        self._device.tx_lo = config["center_frequency_hz"]
        self._device.tx_rf_bandwidth = config["rf_bandwidth_hz"]
        self._device.tx_enabled_channels = [0]
        self._device.tx_cyclic_buffer = True
        self._config = config
        self._status["requested_config"] = dict(config)
        actual = self._actual()
        self._confirm_clock(actual)

    def configure(self, config: dict) -> dict:
        if not isinstance(config, dict):
            raise ValueError("TX-Konfiguration muss ein Objekt sein.")
        with self._lock:
            normalized = validate_tx_config({**self._config, **config})
            if normalized["uri"] != self._config["uri"]:
                raise ValueError("Die Pluto-URI kann bei geöffnetem Adapter nicht geändert werden.")
            self._require_open()
            if self._status["state"] != "muted" or self._receiver is not None:
                raise RuntimeError("TX zuerst stummschalten und gemeinsamen RX schließen.")
            try:
                self._configure_locked(normalized)
            except Exception as exc:
                self._stop_locked("configuration_failed")
                self._status["error"] = str(exc)[:1000]
                raise RuntimeError(str(exc)) from exc
            return self.status()

    def start_cyclic(self, iq_normalized: np.ndarray) -> dict:
        if (not isinstance(iq_normalized, np.ndarray) or iq_normalized.ndim != 1
                or not np.iscomplexobj(iq_normalized) or not 1 <= len(iq_normalized) <= MAX_TX_SAMPLES):
            raise ValueError(f"TX-IQ muss ein eindimensionales komplexes NumPy-Array mit 1–{MAX_TX_SAMPLES} Samples sein.")
        if not np.isfinite(iq_normalized).all():
            raise ValueError("TX-IQ enthält NaN oder unendliche Werte.")
        peak = float(np.max(np.abs(iq_normalized)))
        # Permit only the rounding error of the float32 representation of 0.8.
        if peak > TX_MAX_NORMALIZED_PEAK + 1e-7:
            raise ValueError("TX-IQ-Spitzenamplitude darf höchstens 0,8 betragen; keine automatische Nachnormierung.")
        raw = np.multiply(iq_normalized, np.float32(TX_DIGITAL_SCALE), dtype=np.complex64)
        with self._lock:
            self._require_open()
            if self._status["state"] != "muted" or not self._status["off_acknowledged"]:
                raise RuntimeError("TX zuerst bestätigt stummschalten; ein zyklischer Buffer ist bereits aktiv oder der Zustand unklar.")
            try:
                if not self._read_powerdown():
                    raise RuntimeError("TX-LO ist vor dem Buffer-Upload nicht stummgeschaltet.")
                self._device.tx_enabled_channels = [0]
                self._device.tx_cyclic_buffer = True
                self._device.tx(raw)
                submitted_at, submitted_ns = _utcnow(), time.monotonic_ns()
                self._device.tx_hardwaregain_chan0 = self._config["tx_gain_db"]
                actual = self._actual()
                self._confirm_clock(actual)
                if not math.isclose(actual["tx_gain_db"], self._config["tx_gain_db"], abs_tol=1e-8, rel_tol=0):
                    raise RuntimeError("Die gewünschte TX-Dämpfung wurde nicht zurückgelesen; TX-LO bleibt ausgeschaltet.")
                self._powerdown.value = "0"
                if self._read_powerdown():
                    raise RuntimeError("TX-LO wurde nicht eingeschaltet bestätigt.")
                self._status.update(state="transmitting", tx_active=True, off_acknowledged=False,
                    buffer_destroyed=False, lo_powerdown_readback=False, cleanup_errors=[], error=None,
                    generation_id=uuid.uuid4().hex, tx_sample_count=len(raw), normalized_peak=peak,
                    cyclic_period_seconds=len(raw) / actual["sample_rate"],
                    tx_submitted_at_utc=submitted_at, tx_submitted_monotonic_ns=submitted_ns,
                    tx_enabled_at_utc=_utcnow(), tx_enabled_monotonic_ns=time.monotonic_ns())
            except Exception as exc:
                self._stop_locked("start_failed")
                self._status["error"] = str(exc)[:1000]
                raise RuntimeError(str(exc)) from exc
            return self.status()

    def stop(self, reason="requested") -> dict:
        with self._lock:
            self._stop_locked(reason)
            return self.status()

    def shared_receiver(self, rx_config: dict):
        """Borrow RX from this handle; never change its shared sample rate."""
        if not isinstance(rx_config, dict):
            raise ValueError("RX-Konfiguration muss ein Objekt sein.")
        with self._lock:
            self._require_open()
            if self._status["state"] not in {"muted", "transmitting"}:
                raise RuntimeError("Gemeinsamer RX benötigt einen bekannten Pluto-Zustand.")
            if self._receiver is not None:
                raise RuntimeError("Ein gemeinsamer Pluto-RX ist bereits geöffnet.")
            actual_rate = self._actual()["sample_rate"]
            requested_rate = _number(rx_config, "sample_rate", actual_rate, 520834, 61.44e6, integer=True)
            if requested_rate != actual_rate:
                raise ValueError("Pluto RX und TX benötigen dieselbe tatsächlich eingestellte Sample-Rate.")
            center = _number(rx_config, "center_frequency_hz", self._config["center_frequency_hz"], 325e6, 3.8e9, integer=True)
            gain = _number(rx_config, "gain_db", 10, -3, 71)
            size = _number(rx_config, "block_size", 131072, 16, MAX_RX_SAMPLES, integer=True)
            bandwidth = _number(rx_config, "rf_bandwidth_hz", min(20e6, int(actual_rate * .8)), 200e3, 20e6, integer=True)
            try:
                self._device.rx_destroy_buffer()
                self._device.rx_enabled_channels = [0]
                self._device.rx_lo = center
                self._device.rx_rf_bandwidth = bandwidth
                self._device.gain_control_mode_chan0 = "manual"
                self._device.rx_hardwaregain_chan0 = gain
                self._device.rx_buffer_size = size
                actual_center = float(self._device.rx_lo)
                if not math.isfinite(actual_center) or not 325e6 <= actual_center <= 3.8e9:
                    raise RuntimeError("Ungültiger Pluto-RX-Frequenzreadback.")
                actual_gain = float(self._device.rx_hardwaregain_chan0)
                actual_bandwidth = float(self._device.rx_rf_bandwidth)
                if (not math.isfinite(actual_gain) or not -3 <= actual_gain <= 71
                        or not math.isfinite(actual_bandwidth) or not 200e3 <= actual_bandwidth <= 20e6):
                    raise RuntimeError("Ungültiger Pluto-RX-Gain-/Bandbreitenreadback.")
                self._receiver = _SharedReceiver(self, actual_rate, actual_center)
                self._status.update(rx_open=True, rx_config={"sample_rate": actual_rate,
                    "center_frequency_hz": actual_center, "gain_db": actual_gain,
                    "rf_bandwidth_hz": actual_bandwidth, "block_size": size})
            except Exception as exc:
                self._stop_locked("rx_configuration_failed")
                self._status["error"] = str(exc)[:1000]
                raise RuntimeError(str(exc)) from exc
            return self._receiver

    def close(self) -> dict:
        with self._lock:
            if self._device is None:
                return self.status()
            rx_errors = []
            if self._receiver is not None:
                try:
                    self._receiver.close()
                except Exception as exc:
                    rx_errors.append({"action": "rx_destroy_buffer", "error": str(exc)[:500]})
            self._stop_locked("close")
            self._status["cleanup_errors"].extend(rx_errors)
            if rx_errors:
                self._status.update(off_acknowledged=False, tx_active=None)
            self._status.update(state="closed" if self._status["off_acknowledged"] else "unknown", connected=False)
            self._device = None
            self._powerdown = None
            return self.status()


class _SharedReceiver:
    paced = False
    owns_read_buffers = True

    def __init__(self, owner: PlutoTxAdapter, rate: float, center: float):
        self._owner = owner
        self.sample_rate = rate
        self.center_frequency = center
        self._pending = np.empty(0, np.complex64)
        self._closed = False

    def read(self, count: int) -> np.ndarray:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_RX_SAMPLES:
            raise ValueError(f"RX-Leseumfang muss 1–{MAX_RX_SAMPLES} Samples sein.")
        owner = self._owner
        with owner._lock:
            owner._require_open()
            if self._closed or owner._receiver is not self:
                raise RuntimeError("Gemeinsamer Pluto-RX ist geschlossen.")
            try:
                if not len(self._pending):
                    raw = owner._device.rx()
                    raw = raw[0] if isinstance(raw, list) and len(raw) == 1 else raw
                    raw = np.asarray(raw)
                    if raw.ndim != 1 or not np.iscomplexobj(raw) or not 1 <= len(raw) <= MAX_RX_SAMPLES or not np.isfinite(raw).all():
                        raise RuntimeError("Pluto lieferte ungültige oder nicht endliche RX-IQ-Daten.")
                    self._pending = np.asarray(raw, np.complex64) / np.float32(2048)
                result = self._pending[:count].copy()
                self._pending = self._pending[len(result):]
                return result
            except Exception as exc:
                owner._stop_locked("rx_read_failed")
                owner._status["error"] = str(exc)[:1000]
                raise RuntimeError(str(exc)) from exc

    def close(self):
        owner = self._owner
        with owner._lock:
            if self._closed:
                return
            self._closed = True
            self._pending = np.empty(0, np.complex64)
            owner._receiver = None
            owner._status["rx_open"] = False
            try:
                if owner._device is not None:
                    owner._device.rx_destroy_buffer()
            except Exception as exc:
                owner._stop_locked("rx_close_failed")
                owner._status["error"] = str(exc)[:1000]
                raise RuntimeError(str(exc)) from exc
