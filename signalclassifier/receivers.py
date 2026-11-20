"""Explicit live/replay receivers, spectral detection and bounded IQ capture."""
from __future__ import annotations

import importlib
import math
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy import fft, signal

from .sigmf import FORMATS, SigMFWriter, read_metadata, read_recording, recording_info


def _number(config: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{key} muss eine Zahl sein.")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{key} muss zwischen {minimum:g} und {maximum:g} liegen.")
    return value


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Empfängerkonfiguration muss ein Objekt sein.")
    result = dict(config)
    source = result.setdefault("source", "pluto")
    if source not in {"pluto", "rsp1a", "recording", "synthetic"}:
        raise ValueError("Unbekannte Empfangsquelle.")
    if result.setdefault("mode", "monitor") not in {"monitor", "wideband", "energy"}:
        raise ValueError("Modus muss monitor, wideband oder energy sein.")
    result["sample_rate"] = _number(result, "sample_rate", 2_000_000, 8_000, 61_440_000)
    result["center_frequency_hz"] = _number(result, "center_frequency_hz", 100_000_000, 0, 6_000_000_000)
    result["gain_db"] = _number(result, "gain_db", 30, -20, 100)
    result["threshold_db"] = _number(result, "threshold_db", 12, 3, 60)
    result["duration_seconds"] = _number(result, "duration_seconds", 60, 0.1, 86_400)
    result["max_bytes"] = int(_number(result, "max_bytes", 1024 ** 3, 8, 64 * 1024 ** 3))
    result["block_size"] = int(_number(result, "block_size", 131072, 4096, 1048576))
    result["minimum_channel_bandwidth_hz"] = _number(result, "minimum_channel_bandwidth_hz", 12500, 100, 20000000)
    result["merge_gap_hz"] = _number(result, "merge_gap_hz", 3000, 0, 1000000)
    result["spectrum_refresh_hz"] = _number(result, "spectrum_refresh_hz", 8, 1, 30)
    result["detection_interval_seconds"] = _number(result, "detection_interval_seconds", 0, 0, 1)
    result["max_channels"] = int(_number(result, "max_channels", 8, 1, 8))
    block_bytes = result["block_size"] * 8
    result["queue_capacity_bytes"] = int(_number(result, "queue_capacity_bytes", 64 * 1024 ** 2,
                                                block_bytes, 256 * 1024 ** 2))
    result["synthetic_modulation"] = str(result.get("synthetic_modulation", "FM")).upper()
    if result["synthetic_modulation"] not in {"AM", "FM"}:
        raise ValueError("Synthetische Modulation muss AM oder FM sein.")
    if source == "pluto":
        if not 325e6 <= result["center_frequency_hz"] <= 3.8e9:
            raise ValueError("PlutoSDR unterstützt ab Werk 325 MHz bis 3,8 GHz. Für UKW/Flugfunk RSP1A oder Recording wählen; eine Pluto-Hardwareerweiterung wird nicht automatisch aktiviert.")
        if not 520834 <= result["sample_rate"] <= 61.44e6:
            raise ValueError("Pluto-Abtastrate muss im unterstützten Standardbereich 520834 bis 61440000 S/s liegen.")
        if not -3 <= result["gain_db"] <= 71:
            raise ValueError("Pluto RX-Gain muss zwischen -3 und 71 dB liegen.")
    elif source == "rsp1a":
        if not 1000 <= result["center_frequency_hz"] <= 2e9:
            raise ValueError("RSP1A-Abstimmung muss zwischen 1 kHz und 2 GHz liegen.")
        if result["sample_rate"] > 10e6:
            raise ValueError("RSP1A unterstützt höchstens 10 MS/s.")
    if source == "recording" and not result.get("recording_path"):
        raise ValueError("Bitte eine importierte SigMF-Aufnahme auswählen.")
    return result


def spectrum_and_detections(iq: np.ndarray, sample_rate: float, center_frequency: float,
                            threshold_db: float = 12, fft_size: int = 2048, merge_gap_hz: float = 3000) -> dict:
    """Window-normalized digital spectrum; threshold is relative to bin median.

    A unit-amplitude complex tone at a bin center has a peak of 0 dBFS. The
    displayed noise floor is per FFT bin, not a calibrated RF power nor SNR.
    """
    data = np.asarray(iq, np.complex64)
    if data.ndim != 1 or len(data) < 16 or not np.isfinite(data).all():
        raise ValueError("Zu wenige oder ungültige IQ-Daten für die Spektralanalyse.")
    fft_size = min(fft_size, 2 ** int(math.log2(len(data))))
    # Bound FFT CPU cost independently of hardware buffer size.
    windows = min(32, len(data) // fft_size)
    frames = data[:windows * fft_size].reshape(windows, fft_size)
    window = signal.windows.hann(fft_size, sym=False)
    bins = np.fft.fftshift(np.fft.fft(frames * window, axis=1), axes=1)
    power = np.mean(np.abs(bins) ** 2, axis=0) / max(window.sum() ** 2, 1)
    db = 10 * np.log10(np.maximum(power, 1e-20))
    floor = float(np.median(db))
    mask = db > floor + threshold_db
    # Fill isolated spectral gaps inside one occupied band, while keeping
    # clearly separated signals distinct. Edges remain explicit.
    gap_limit = min(64, max(0, int(math.floor(merge_gap_hz / (sample_rate / fft_size)))))
    false_edges = np.diff(np.r_[False, ~mask, False].astype(np.int8))
    for gap_start, gap_end in zip(np.flatnonzero(false_edges == 1), np.flatnonzero(false_edges == -1)):
        if gap_start > 0 and gap_end < fft_size and gap_end - gap_start <= gap_limit:
            mask[gap_start:gap_end] = True
    boundaries = np.diff(np.r_[False, mask, False].astype(np.int8))
    starts, ends = np.flatnonzero(boundaries == 1), np.flatnonzero(boundaries == -1)
    frequencies = center_frequency + np.fft.fftshift(np.fft.fftfreq(fft_size, 1 / sample_rate))
    width = sample_rate / fft_size
    detections = []
    for start, end in zip(starts, ends):
        if end <= start:
            continue
        peak_index = int(start + np.argmax(db[start:end]))
        low = max(center_frequency - sample_rate / 2, float(frequencies[start] - width / 2))
        high = min(center_frequency + sample_rate / 2, float(frequencies[end - 1] + width / 2))
        detections.append({
            "frequency_hz": (low + high) / 2,
            "center_frequency_hz": (low + high) / 2,
            "frequency_min_hz": low,
            "frequency_max_hz": high,
            "bandwidth_hz": high - low,
            "peak_dbfs": float(db[peak_index]),
            "excess_db": float(db[peak_index] - floor),
            "noise_floor_dbfs": floor,
            "snr_db": None,
            "method": "median_bin_energy",
        })
    detections.sort(key=lambda item: item["peak_dbfs"], reverse=True)
    return {"frequencies_hz": frequencies.tolist(), "power_dbfs": db.tolist(),
            "noise_floor_dbfs": floor, "threshold_db": threshold_db,
            "fft_size": fft_size, "detections": detections[:16],
            "analysis_sample_count": windows * fft_size, "analysis_sample_offset": 0,
            "analysis_duration_seconds": windows * fft_size / sample_rate,
            "power_reference": "Digitaler FFT-Bin-Pegel; 0 dBFS = komplexer Ton mit Amplitude 1. Nicht in dBm kalibriert.",
            "detection_note": "Schwelle über Median der FFT-Bins; excess_db ist kein kalibriertes SNR."}


class _Synthetic:
    def __init__(self, config: dict):
        self.config = config
        self.position = 0
        self.rng = np.random.default_rng(int(config.get("seed", 42)))
        self.sample_rate = config["sample_rate"]
        self.center_frequency = config["center_frequency_hz"]
        self.paced = True
        self.phase = 0.0

    def read(self, count: int) -> np.ndarray:
        t = (self.position + np.arange(count)) / self.sample_rate
        audio = 0.7 * np.sin(2 * np.pi * 931 * t) + 0.3 * np.sin(2 * np.pi * 2137 * t)
        offset = min(0.12 * self.sample_rate, 150000)
        if self.config["synthetic_modulation"] == "AM":
            iq = 0.4 * (1 + 0.7 * audio) * np.exp(2j * np.pi * offset * t)
        else:
            deviation = min(75_000, self.sample_rate * 0.12)
            phases = self.phase + np.cumsum(2 * np.pi * (offset + deviation * audio) / self.sample_rate)
            self.phase = float(phases[-1] % (2 * np.pi))
            iq = 0.65 * np.exp(1j * phases)
        iq += 0.015 * (self.rng.standard_normal(count) + 1j * self.rng.standard_normal(count))
        self.position += count
        return iq.astype(np.complex64)

    def close(self):
        pass


class _Replay:
    def __init__(self, config: dict):
        self.path = Path(config["recording_path"])
        self.metadata = read_metadata(self.path)
        self.info = recording_info(self.path)
        self.sample_rate = self.info["sample_rate"]
        self.center_frequency = self.info["center_frequency_hz"]
        self.position = 0
        self.paced = True

    def read(self, count: int) -> np.ndarray:
        if self.position >= self.info["samples"]:
            return np.empty(0, np.complex64)
        capture_end = self.info["samples"]
        for capture in self.metadata["captures"]:
            index = capture["core:sample_start"]
            if index <= self.position:
                self.center_frequency = float(capture.get("core:frequency", 0))
            else:
                capture_end = index
                break
        result, _ = read_recording(self.path, self.position, min(count, capture_end - self.position))
        self.position += len(result)
        return result

    def close(self):
        pass


class _Pluto:
    # rx() is converted/scaled into a new array; it is never reused by libiio.
    owns_read_buffers = True

    def __init__(self, config: dict):
        try:
            adi = importlib.import_module("adi")
        except ImportError as exc:
            raise RuntimeError("Pluto-Treiber fehlt: pyadi-iio und libiio installieren; siehe docs/aufnahme.md.") from exc
        self.device = adi.Pluto(uri=config.get("uri") or "ip:192.168.2.1")
        try:
            # Bound blocking receive time, allowing clean shutdown on disconnect.
            if hasattr(self.device, "_ctx"):
                self.device._ctx.set_timeout(1500)
            self.device.rx_enabled_channels = [0]
            self.device.sample_rate = int(config["sample_rate"])
            self.device.rx_lo = int(config["center_frequency_hz"])
            self.device.rx_rf_bandwidth = int(min(20e6, max(200e3, config["sample_rate"] * 0.8)))
            self.device.gain_control_mode_chan0 = "manual"
            self.device.rx_hardwaregain_chan0 = config["gain_db"]
            self.device.rx_buffer_size = config["block_size"]
            self.sample_rate = float(self.device.sample_rate)
            self.center_frequency = float(self.device.rx_lo)
            self.paced = False
        except Exception:
            self.close()
            raise

    def read(self, count: int) -> np.ndarray:
        result = self.device.rx()
        if isinstance(result, list):
            result = result[0]
        # AD9363 raw signed 12-bit component scale (not RF-calibrated dBm).
        return np.asarray(result, np.complex64) / np.float32(2048)

    def close(self):
        try:
            self.device.rx_destroy_buffer()
        except (AttributeError, OSError, RuntimeError):
            pass


class _RSP1A:
    # readStream() receives a newly allocated NumPy array on each call.
    owns_read_buffers = True

    def __init__(self, config: dict):
        try:
            self.soapy = importlib.import_module("SoapySDR")
        except ImportError as exc:
            raise RuntimeError("RSP1A-Treiber fehlt: SDRplay API, SoapySDR und SoapySDRPlay3 installieren; siehe docs/aufnahme.md.") from exc
        self.stream = None
        self.device = None
        self.timeouts = 0
        args = config.get("uri") or "driver=sdrplay"
        self.device = self.soapy.Device(args)
        rx = self.soapy.SOAPY_SDR_RX
        try:
            self.device.setSampleRate(rx, 0, config["sample_rate"])
            self.device.setFrequency(rx, 0, config["center_frequency_hz"])
            self.device.setGainMode(rx, 0, False)
            self.device.setGain(rx, 0, config["gain_db"])
            self.sample_rate = float(self.device.getSampleRate(rx, 0))
            self.center_frequency = float(self.device.getFrequency(rx, 0))
            if abs(self.sample_rate - config["sample_rate"]) > 0.001 * config["sample_rate"]:
                raise RuntimeError(f"RSP-Treiber hat {self.sample_rate:g} S/s statt der gewünschten Rate eingestellt. Eine unterstützte Abtastrate auswählen.")
            self.stream = self.device.setupStream(rx, self.soapy.SOAPY_SDR_CF32, [0])
            result = self.device.activateStream(self.stream)
            if result:
                raise RuntimeError(f"SoapySDR konnte Stream nicht starten (Code {result}).")
            self.paced = False
        except Exception:
            self.close()
            raise

    def read(self, count: int) -> np.ndarray | None:
        result = np.empty(count, np.complex64)
        response = self.device.readStream(self.stream, [result], count, timeoutUs=250_000)
        if response.ret == getattr(self.soapy, "SOAPY_SDR_TIMEOUT", -1):
            self.timeouts += 1
            if self.timeouts >= 20:
                raise RuntimeError("RSP1A liefert seit 5 Sekunden keine IQ-Daten.")
            return None
        self.timeouts = 0
        if response.ret < 0:
            overflow = response.ret == getattr(self.soapy, "SOAPY_SDR_OVERFLOW", -4)
            corruption = response.ret == getattr(self.soapy, "SOAPY_SDR_CORRUPTION", -3)
            raise _CaptureFailure(f"SoapySDR-Streamfehler {response.ret}; Empfang beendet, statt eine Datenlücke zu übergehen.",
                                  reason="receiver_overflow" if overflow else "receiver_error",
                                  reported_gap=overflow or corruption, overflow=overflow)
        if response.ret == 0:
            return None
        return result[:response.ret]

    def close(self):
        if self.device is not None and self.stream is not None:
            try:
                self.device.deactivateStream(self.stream)
            finally:
                self.device.closeStream(self.stream)
                self.stream = None


class _CaptureFailure(RuntimeError):
    def __init__(self, message, *, reason="receiver_error", reported_gap=False, overflow=False):
        super().__init__(message)
        self.reason = reason
        self.reported_gap = reported_gap
        self.overflow = overflow


class _IQQueue(queue.Queue):
    """Queue accounting changes atomically with admission/removal, not later."""
    def __init__(self, capacity):
        super().__init__(maxsize=capacity)
        self.byte_count = 0
        self.high_water_blocks = 0
        self.high_water_bytes = 0

    def _put(self, item):
        super()._put(item)
        self.byte_count += item[0].nbytes
        self.high_water_blocks = max(self.high_water_blocks, self._qsize())
        self.high_water_bytes = max(self.high_water_bytes, self.byte_count)

    def _get(self):
        item = super()._get()
        self.byte_count -= item[0].nbytes
        return item

    def metrics(self):
        with self.mutex:
            return {"queue_blocks": self._qsize(), "queue_bytes": self.byte_count,
                    "queue_high_water_blocks": self.high_water_blocks,
                    "queue_high_water_bytes": self.high_water_bytes}


class _Channelizer:
    """Stateful causal FIR downconverter with a continuous decimation phase."""
    def __init__(self, input_rate: float, offset: float, bandwidth: float):
        self.input_rate = input_rate
        self.offset = offset
        self.bandwidth = bandwidth
        self.decimation = max(1, int(input_rate / (2 * bandwidth)))
        self.output_rate = input_rate / self.decimation
        # Transition width scales with decimation; overlap-save avoids O(N*D)
        # time at a narrow occupied channel inside a wide receiver window.
        taps = min(131073, 32 * self.decimation + 1)
        if taps % 2 == 0:
            taps += 1
        self.taps = signal.firwin(taps, cutoff=min(0.45 * self.output_rate, 0.49 * input_rate), fs=input_rate).astype(np.float32)
        self.tail = np.zeros(len(self.taps) - 1, np.complex64)
        self.position = 0
        self.phase = 0.0
        self._oscillator = np.empty(0, np.complex64)
        self._kernel_fft = None
        self._fft_size = 0

    def process(self, iq: np.ndarray) -> np.ndarray:
        size = len(iq)
        if len(self._oscillator) != size:
            self._oscillator = np.exp(-2j * np.pi * (self.offset / self.input_rate) * np.arange(size)).astype(np.complex64)
        mixed = np.asarray(iq, np.complex64) * self._oscillator
        mixed *= np.complex64(np.exp(1j * self.phase))
        self.phase = float((self.phase - 2 * np.pi * (self.offset / self.input_rate) * size) % (2 * np.pi))
        # Pad by at most D-1 samples so every short-IFFT output is aligned to
        # an absolute input index divisible by D, even across irregular blocks.
        history = len(self.tail)
        padding = (self.position - history) % self.decimation
        joined = np.concatenate((np.zeros(padding, np.complex64), self.tail, mixed))
        convolution_size = len(joined) + len(self.taps) - 1
        fft_size = self.decimation * fft.next_fast_len((convolution_size + self.decimation - 1) // self.decimation)
        if fft_size != self._fft_size:
            self._fft_size = fft_size
            self._kernel_fft = fft.fft(self.taps, fft_size)
        transformed = fft.fft(joined, fft_size)
        transformed *= self._kernel_fft
        # Frequency-domain folding equals exact time-domain decimation. Only
        # the short output IFFT is computed, rather than discarding D-1 of D
        # full-rate samples after a full-size inverse FFT.
        folded = transformed.reshape(self.decimation, -1).sum(axis=0)
        decimated = fft.ifft(folded) / np.float32(self.decimation)
        first = (padding + history + self.decimation - 1) // self.decimation
        end = (padding + history + size + self.decimation - 1) // self.decimation
        output = decimated[first:end]
        self.tail = joined[-history:].copy()
        self.position += len(iq)
        return output


class CaptureManager:
    """One receiver, bounded hardware read-ahead and ordered DSP/storage."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.recordings_dir = self.data_dir / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._reader_done = threading.Event()
        self._capture_queue: queue.Queue | None = None
        self._reader_reason = None
        self._queued_bytes = 0
        self._performance = self._empty_performance()
        self._processing_seconds = 0.0
        self._rf_seconds = 0.0
        self._writer_seconds_block = 0.0
        self._receiver = None
        self._config: dict = {}
        self._state = "stopped"
        self._error: str | None = None
        self._reason: str | None = None
        self._started_at: str | None = None
        self._elapsed = 0.0
        self._samples = 0
        self._bytes = 0
        self._saved: list[str] = []
        self._snapshot: dict = {"frequencies_hz": [], "power_dbfs": [], "detections": [], "classification": None}
        self.latest_iq = np.empty(0, np.complex64)
        self.latest_sample_rate = 0.0
        self.latest_center_frequency = 0.0
        self._session_id = ""

    @staticmethod
    def _empty_performance():
        return {"reader_ms": 0.0, "processing_ms": 0.0, "writer_ms": 0.0,
                "processing_rtf": 0.0, "processing_rtf_mean": 0.0,
                "queue_blocks": 0, "queue_capacity_blocks": 0,
                "queue_bytes": 0, "queue_capacity_bytes": 0,
                "queue_high_water_blocks": 0, "queue_high_water_bytes": 0,
                "overflows": 0, "gaps": 0, "actual_sample_rate": 0.0,
                "capture_error": None, "processed_blocks": 0, "read_blocks": 0,
                "source_samples_read": 0, "source_samples_processed": 0,
                "detection_interval_seconds": 0.0, "publication_hz": 8.0,
                "reader_queue_enabled": False, "gap_free_verified": False}

    def start(self, config: dict) -> dict:
        with self._lock:
            if ((self._thread is not None and self._thread.is_alive())
                    or (self._reader_thread is not None and self._reader_thread.is_alive())):
                raise ValueError("Ein Empfänger läuft bereits; zuerst stoppen.")
            config = validate_config(config)
            self._error = None
            self._reason = None
            self._config = config
            self._state = "starting"
            self._performance = self._empty_performance()
            self._started_at = None
            self.latest_iq = np.empty(0, np.complex64)
            self.latest_sample_rate = 0.0
            self.latest_center_frequency = 0.0
            self._snapshot = {"frequencies_hz": [], "power_dbfs": [], "detections": [], "classification": None}
            try:
                factory = {"pluto": _Pluto, "rsp1a": _RSP1A, "recording": _Replay, "synthetic": _Synthetic}[config["source"]]
                self._receiver = factory(config)
            except Exception as exc:
                self._state = "error"
                self._error = str(exc)
                raise RuntimeError(str(exc)) from exc
            config["sample_rate"] = self._receiver.sample_rate
            config["center_frequency_hz"] = self._receiver.center_frequency
            self._processing_seconds = 0.0
            self._rf_seconds = 0.0
            self._queued_bytes = 0
            self._reader_reason = None
            self._reader_stop.clear()
            self._reader_done.clear()
            queue_enabled = config["source"] in {"pluto", "rsp1a"}
            capacity = max(1, config["queue_capacity_bytes"] // (config["block_size"] * 8))
            self._capture_queue = _IQQueue(capacity) if queue_enabled else None
            self._performance.update({"reader_queue_enabled": queue_enabled,
                                      "queue_capacity_blocks": capacity if queue_enabled else 0,
                                      "queue_capacity_bytes": capacity * config["block_size"] * 8 if queue_enabled else 0,
                                      "actual_sample_rate": self._receiver.sample_rate,
                                      "detection_interval_seconds": max(config["detection_interval_seconds"],
                                                                         config["block_size"] / self._receiver.sample_rate) if config["mode"] == "energy" else 1 / config["spectrum_refresh_hz"],
                                      "publication_hz": config["spectrum_refresh_hz"]})
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._samples = 0
            self._bytes = 0
            self._elapsed = 0.0
            self._saved = []
            self.latest_iq = np.empty(0, np.complex64)
            self.latest_sample_rate = config["sample_rate"]
            self.latest_center_frequency = config["center_frequency_hz"]
            self._snapshot = {"frequencies_hz": [], "power_dbfs": [], "detections": [], "classification": None}
            self._session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
            self._stop.clear()
            self._state = "running"
            self._thread = threading.Thread(target=self._run, name="signalclassifier-capture", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        self._stop.set()
        self._reader_stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=6)
        with self._lock:
            if thread is not None and thread.is_alive():
                self._error = "Empfänger reagiert nicht auf Stop; Treiberverbindung prüfen. Kein weiterer Stream wird parallel gestartet."
                self._state = "error"
            elif self._state == "running":
                self._state = "stopped"
            return self.status()

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state, "running": self._state == "running",
                    "busy": self._state in ("starting", "running") or any(
                        worker is not None and worker.is_alive() for worker in (self._thread, self._reader_thread)),
                    "source": self._config.get("source"), "mode": self._config.get("mode", "monitor"),
                    "error": self._error, "reason": self._reason,
                    "config": {k: v for k, v in self._config.items() if k != "recording_path"},
                    "sample_rate": self._config.get("sample_rate"),
                    "center_frequency_hz": self._config.get("center_frequency_hz"),
                    "started_at": self._started_at, "elapsed_seconds": self._elapsed,
                    "samples": self._samples, "bytes_written": self._bytes,
                    "recordings_saved": list(self._saved), "session_id": self._session_id,
                    "synthetic": self._config.get("source") == "synthetic",
                    "hardware_verified": False,
                    "gap_free_verified": False,
                    "performance": dict(self._performance, **(self._capture_queue.metrics() if self._capture_queue else {}),
                                        capture_error=self._error),
                    "note": "Softwareadapter vorhanden; physischer Empfang und kontinuierlicher Maximaldurchsatz sind am eigenen Aufbau zu prüfen."}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot, receiver=self.status())

    def inference_segment(self) -> tuple[np.ndarray, float, float]:
        """Copy one consistent IQ/rate/frequency context for a classifier."""
        with self._lock:
            return self.latest_iq.copy(), self.latest_sample_rate, self.latest_center_frequency

    def list_recordings(self) -> list[dict]:
        result = []
        for path in sorted(self.recordings_dir.glob("*.sigmf-meta"), reverse=True):
            try:
                result.append(recording_info(path))
            except (ValueError, OSError, TypeError) as exc:
                result.append({"id": path.stem, "name": path.stem, "error": str(exc), "metadata_path": str(path)})
        return result

    def _new_writer(self, suffix: str, rate: float, center: float, extras: dict | None = None) -> SigMFWriter:
        metadata = {"sc:source": self._config["source"], "sc:mode": self._config["mode"],
                    "sc:session_id": self._session_id, "signalclassifier:session_id": self._session_id,
                    "sc:requested_gain_db": self._config["gain_db"],
                    "sc:gap_free_verified": False,
                    "sc:digital_scale": "unit complex amplitude; hardware path uncalibrated",
                    "sc:synthetic_modulation": self._config["synthetic_modulation"] if self._config["source"] == "synthetic" else None}
        if self._config["source"] == "recording":
            metadata["sc:parent_recording_id"] = Path(self._config["recording_path"]).stem
            metadata["signalclassifier:parent_recording_id"] = metadata["sc:parent_recording_id"]
            origin_group = self._receiver.info["group_id"]
            metadata["sc:origin_group_id"] = origin_group
            metadata["signalclassifier:group_id"] = origin_group
        else:
            metadata["signalclassifier:group_id"] = self._session_id
        metadata.update(extras or {})
        return SigMFWriter(self.recordings_dir / f"{self._session_id}-{suffix}", rate, center, extra=metadata)

    def _finish_writer(self, writer: SigMFWriter, reason: str, source_stop: int | None = None):
        with self._lock:
            # Gate close occurs before the enclosing block commits its global
            # counters. The file already includes that block's source prefix.
            processed = max(self._samples, source_stop or 0)
            integrity = {"gap_free_verified": False, "reported_gaps": self._performance["gaps"],
                         "overflows": self._performance["overflows"], "capture_error": self._error,
                         "stop_reason": reason, "source_samples_read": self._performance["source_samples_read"],
                         "source_samples_processed": processed}
            performance = dict(self._performance, **(self._capture_queue.metrics() if self._capture_queue else {}),
                               capture_error=self._error, source_samples_processed=processed)
        extra = {"sc:stop_reason": reason, "sc:integrity": integrity, "sc:performance": performance}
        if source_stop is not None:
            extra["sc:source_sample_stop"] = source_stop
        path = writer.close(extra)
        if writer.samples == 0:
            # No phantom recording for an empty stream or an exhausted budget.
            path.unlink(missing_ok=True)
            writer.data_path.unlink(missing_ok=True)
            return
        with self._lock:
            self._saved.append(path.stem)

    def _append_bounded(self, writer: SigMFWriter, iq: np.ndarray) -> bool:
        available = max(0, (self._config["max_bytes"] - self._bytes) // 8)
        segment = iq[:available]
        if len(segment):
            start = time.perf_counter()
            try:
                written = writer.append(segment)
            except OSError as exc:
                raise _CaptureFailure(f"IQ-Datei konnte nicht geschrieben werden: {exc}", reason="writer_error") from exc
            finally:
                self._writer_seconds_block += time.perf_counter() - start
            with self._lock:
                self._bytes += written
        return len(segment) == len(iq)

    def _record_failure(self, exc):
        with self._lock:
            self._error = str(exc)
            self._reader_reason = getattr(exc, "reason", "receiver_error")
            self._performance["capture_error"] = str(exc)
            self._performance["gaps"] += int(bool(getattr(exc, "reported_gap", False)))
            self._performance["overflows"] += int(bool(getattr(exc, "overflow", False)))

    def _read_hardware(self, receiver):
        """Drain hardware independently of FFT/channelization/disk latency.

        Admission is nonblocking and bounded. A full queue stops at the first
        rejected block; the DSP drains only the already admitted prefix.
        """
        position = 0
        started = time.monotonic()
        reason = "user_stop"
        try:
            while not self._stop.is_set() and not self._reader_stop.is_set():
                if time.monotonic() - started >= self._config["duration_seconds"]:
                    reason = "duration_limit"
                    break
                read_start = time.perf_counter()
                raw = receiver.read(self._config["block_size"])
                elapsed = time.perf_counter() - read_start
                received_at = datetime.now(timezone.utc).isoformat()
                with self._lock:
                    self._performance["reader_ms"] = elapsed * 1000
                if raw is None:
                    continue
                if not len(raw):
                    reason = "end_of_recording"
                    break
                if len(raw) > self._config["block_size"]:
                    raise _CaptureFailure("Empfänger lieferte einen größeren Block als das konfigurierte Queue-Speicherbudget zulässt.")
                rate, center = float(receiver.sample_rate), float(receiver.center_frequency)
                remaining = max(0, int(self._config["duration_seconds"] * rate) - position)
                iq = np.asarray(raw[:remaining], np.complex64)
                if not len(iq):
                    reason = "duration_limit"
                    break
                if not getattr(receiver, "owns_read_buffers", False):
                    # Unknown/borrowed driver storage may be reused on read().
                    iq = iq.copy()
                with self._lock:
                    self._performance["read_blocks"] += 1
                    self._performance["source_samples_read"] += len(iq)
                    self._performance["actual_sample_rate"] = rate
                if self._stop.is_set() or self._reader_stop.is_set():
                    break
                with self._lock:
                    try:
                        self._capture_queue.put_nowait((iq, rate, center, position, received_at))
                    except queue.Full as exc:
                        raise _CaptureFailure("IQ-Eingangsqueue voll: Verarbeitung oder Datenträger ist langsamer als der Empfang. Aufnahme am letzten zusammenhängenden Präfix beendet; keine Blöcke werden still übersprungen.",
                                              reason="queue_overflow", reported_gap=True, overflow=True) from exc
                    self._performance.update(self._capture_queue.metrics())
                position += len(iq)
        except Exception as exc:
            self._record_failure(exc)
            reason = getattr(exc, "reason", "receiver_error")
        finally:
            try:
                receiver.close()
            except Exception as exc:
                self._record_failure(exc)
                reason = "receiver_error"
            with self._lock:
                self._reader_reason = reason
            self._reader_done.set()

    def _run(self):
        receiver = self._receiver
        started = time.monotonic()
        wideband: SigMFWriter | None = None
        channels: list[dict] = []
        channel_counter = 0
        position = 0
        display_channel = None
        inference_context = None
        inference_iq = np.empty(0, np.complex64)
        last_center = receiver.center_frequency
        last_rate = receiver.sample_rate
        last_analysis_rf = -math.inf
        last_publish_rf = -math.inf
        source_seconds = 0.0
        analysis = None
        analysis_position = 0
        analysis_block_sample_count = 0
        analysis_received_at = None
        analysis_ended_at = None
        reason = "user_stop"
        queued = self._capture_queue is not None
        try:
            if self._config["mode"] == "wideband":
                wideband = self._new_writer("wideband", receiver.sample_rate, receiver.center_frequency)
            if queued:
                self._reader_thread = threading.Thread(target=self._read_hardware, args=(receiver,),
                                                       name="signalclassifier-hardware-reader", daemon=True)
                self._reader_thread.start()
            while not self._stop.is_set():
                if not queued and time.monotonic() - started >= self._config["duration_seconds"]:
                    reason = "duration_limit"
                    break
                if self._config["mode"] != "monitor" and self._bytes >= self._config["max_bytes"]:
                    reason = "storage_limit"
                    break
                read_start = time.perf_counter()
                if queued:
                    if self._reader_done.is_set() and self._capture_queue.empty():
                        reason = self._reader_reason or "end_of_recording"
                        break
                    try:
                        iq, sample_rate, center, block_position, received_at = self._capture_queue.get(timeout=0.05)
                    except queue.Empty:
                        if self._reader_done.is_set():
                            reason = self._reader_reason or "end_of_recording"
                            break
                        continue
                    if block_position != position:
                        raise _CaptureFailure("Nicht zusammenhängende IQ-Sampleindizes in der Eingangsqueue.",
                                              reason="sample_gap", reported_gap=True)
                else:
                    iq = receiver.read(self._config["block_size"])
                    received_at = datetime.now(timezone.utc).isoformat()
                    with self._lock:
                        self._performance["reader_ms"] = (time.perf_counter() - read_start) * 1000
                    if iq is None:
                        continue
                    if not len(iq):
                        reason = "end_of_recording"
                        break
                    sample_rate, center = float(receiver.sample_rate), float(receiver.center_frequency)
                    remaining = max(0, int(self._config["duration_seconds"] * sample_rate) - position)
                    iq = np.asarray(iq[:remaining], np.complex64)
                    if not len(iq):
                        reason = "duration_limit"
                        break
                    with self._lock:
                        self._performance["read_blocks"] += 1
                        self._performance["source_samples_read"] += len(iq)
                        self._performance["actual_sample_rate"] = sample_rate
                processing_start = time.perf_counter()
                self._writer_seconds_block = 0.0
                if not np.isfinite(iq).all():
                    raise _CaptureFailure("Empfänger lieferte NaN oder unendliche IQ-Werte.",
                                          reason="invalid_samples", reported_gap=True)
                retuned = center != last_center or sample_rate != last_rate
                if retuned:
                    if wideband:
                        if sample_rate != last_rate:
                            # Single-file rate changes are outside this SigMF subset.
                            self._finish_writer(wideband, "sample_rate_changed", position)
                            wideband = self._new_writer(f"wideband-{position}", sample_rate, center,
                                                        {"sc:source_sample_start": position})
                        else:
                            wideband.metadata["captures"].append({"core:sample_start": wideband.samples,
                                                                  "core:frequency": center})
                    for channel in channels:
                        self._finish_writer(channel["writer"], "retune", channel["source_stop"])
                    channels = []
                    display_channel = None
                    last_center, last_rate = center, sample_rate
                interval = (self._config["detection_interval_seconds"] if self._config["mode"] == "energy"
                            else 1 / self._config["spectrum_refresh_hz"])
                analyzed_now = analysis is None or retuned or source_seconds - last_analysis_rf >= interval
                if analyzed_now:
                    analysis = spectrum_and_detections(iq, sample_rate, center, self._config["threshold_db"],
                                                       merge_gap_hz=self._config["merge_gap_hz"])
                    last_analysis_rf = source_seconds
                    analysis_position = position
                    analysis_block_sample_count = len(iq)
                    analysis_received_at = received_at
                    unanalyzed_tail = len(iq) - analysis["analysis_sample_count"]
                    analysis_ended_at = (datetime.fromisoformat(received_at)
                                         - timedelta(seconds=unanalyzed_tail / sample_rate)).isoformat()
                quota_reached = False
                if wideband and not self._append_bounded(wideband, iq):
                    quota_reached = True
                channel_outputs = {}
                if self._config["mode"] == "energy":
                    matched = set()
                    for detection in (analysis["detections"][:self._config["max_channels"]] if analyzed_now else []):
                        frequency = detection["frequency_hz"]
                        bin_width = sample_rate / analysis["fft_size"]
                        bandwidth = min(sample_rate * 0.9,
                                        max(self._config["minimum_channel_bandwidth_hz"], 8 * bin_width,
                                            detection["bandwidth_hz"] * 1.5))
                        existing = next((c for c in channels if id(c) not in matched
                                         and abs(c["frequency"] - frequency) < max(c["bandwidth"] / 2, 4 * bin_width)), None)
                        if existing is not None and bandwidth > existing["bandwidth"] * 1.5:
                            self._finish_writer(existing["writer"], "channel_bandwidth_changed", existing["source_stop"])
                            channels.remove(existing)
                            existing = None
                        if existing is None:
                            if len(channels) >= self._config["max_channels"]:
                                continue
                            channel_counter += 1
                            extractor = _Channelizer(sample_rate, frequency - center, bandwidth)
                            writer = self._new_writer(f"channel-{channel_counter:04d}", extractor.output_rate, frequency,
                                {"sc:parent_center_frequency_hz": center,
                                 "sc:parent_sample_rate": sample_rate,
                                 "sc:source_sample_start": position,
                                 "sc:selected_bandwidth_hz": bandwidth,
                                 "sc:decimation": extractor.decimation,
                                 "sc:fir_group_delay_input_samples": (len(extractor.taps) - 1) / 2,
                                 "sc:detection_threshold_db": self._config["threshold_db"],
                                 "sc:detection_method": "median_bin_energy",
                                 "sc:detection_interval_seconds": max(interval, len(iq) / sample_rate),
                                 "sc:pretrigger_seconds": 0,
                                 "sc:posttrigger_seconds": 0.25})
                            existing = {"frequency": frequency, "bandwidth": bandwidth, "extractor": extractor,
                                        "writer": writer, "last_seen": position, "source_stop": position}
                            channels.append(existing)
                        existing["last_seen"] = position + len(iq)
                        matched.add(id(existing))
                    for channel in list(channels):
                        output = channel["extractor"].process(iq)
                        before = channel["writer"].samples
                        complete = self._append_bounded(channel["writer"], output)
                        channel["source_stop"] = position + (len(iq) if complete else
                            min(len(iq), (channel["writer"].samples - before) * channel["extractor"].decimation))
                        channel_outputs[channel["extractor"]] = output
                        if not complete:
                            quota_reached = True
                        if analyzed_now and position + len(iq) - channel["last_seen"] > sample_rate * 0.25:
                            self._finish_writer(channel["writer"], "energy_gate_closed", channel["source_stop"])
                            channels.remove(channel)
                strongest = analysis["detections"][0] if analysis["detections"] else None
                if strongest:
                    # Reuse the exact already-filtered strongest recording channel.
                    # Previously the same full-rate FIR/FFT ran a ninth time here.
                    recorded = next((c for c in channels
                                     if abs(c["frequency"] - strongest["frequency_hz"]) < c["bandwidth"] / 2), None)
                    if recorded is not None:
                        display_channel = {"frequency": recorded["frequency"], "bandwidth": recorded["bandwidth"],
                                           "extractor": recorded["extractor"]}
                        latest_iq = channel_outputs[recorded["extractor"]]
                    else:
                        bandwidth = min(sample_rate * 0.9,
                                        max(self._config["minimum_channel_bandwidth_hz"],
                                            sample_rate / analysis["fft_size"] * 8, strongest["bandwidth_hz"] * 1.5))
                        if (display_channel is None
                                or abs(display_channel["frequency"] - strongest["frequency_hz"]) > display_channel["bandwidth"] / 2
                                or display_channel["extractor"].input_rate != sample_rate
                                or bandwidth > display_channel["bandwidth"] * 1.5
                                or display_channel["extractor"] in channel_outputs):
                            display_channel = {"frequency": strongest["frequency_hz"], "bandwidth": bandwidth,
                                               "extractor": _Channelizer(sample_rate, strongest["frequency_hz"] - center, bandwidth)}
                        latest_iq = display_channel["extractor"].process(iq)
                    latest_rate = display_channel["extractor"].output_rate
                    latest_center = display_channel["frequency"]
                else:
                    display_channel = None
                    latest_iq, latest_rate, latest_center = iq, sample_rate, center
                context = (display_channel["extractor"] if display_channel else None, latest_rate, latest_center)
                if context != inference_context:
                    inference_iq = np.empty(0, np.complex64)
                    inference_context = context
                if len(latest_iq) >= 65536:
                    inference_iq = latest_iq[-65536:].copy()
                else:
                    inference_iq = np.concatenate((inference_iq, latest_iq))[-65536:]
                position += len(iq)
                source_seconds += len(iq) / sample_rate
                processing_seconds = time.perf_counter() - processing_start
                with self._lock:
                    self.latest_iq = inference_iq.copy()
                    self.latest_sample_rate, self.latest_center_frequency = latest_rate, latest_center
                    self._samples = position
                    self._elapsed = time.monotonic() - started
                    self._processing_seconds += processing_seconds
                    self._rf_seconds += len(iq) / sample_rate
                    previous_rtf = self._performance["processing_rtf"]
                    instantaneous_rtf = processing_seconds / (len(iq) / sample_rate)
                    self._performance.update({"processing_ms": processing_seconds * 1000,
                                              "writer_ms": self._writer_seconds_block * 1000,
                                              "processing_rtf": instantaneous_rtf if not self._performance["processed_blocks"] else
                                                                0.2 * instantaneous_rtf + 0.8 * previous_rtf,
                                              "processing_rtf_mean": self._processing_seconds / self._rf_seconds,
                                              "source_samples_processed": position})
                    self._performance["processed_blocks"] += 1
                    if source_seconds - last_publish_rf >= 1 / self._config["spectrum_refresh_hz"] or retuned:
                        self._snapshot = dict(analysis, sample_rate=sample_rate, center_frequency_hz=center,
                            source=self._config["source"], mode=self._config["mode"],
                            synthetic=self._config["source"] == "synthetic", sample_index=analysis_position,
                            block_sample_count=analysis_block_sample_count,
                            block_duration_seconds=analysis_block_sample_count / sample_rate,
                            timestamp=analysis_ended_at, analysis_timestamp=analysis_ended_at,
                            block_received_at=analysis_received_at,
                            timestamp_source=("host_receive_end_utc" if analysis_block_sample_count == analysis["analysis_sample_count"]
                                              else "host_receive_end_minus_unanalyzed_tail"),
                            rf_timestamp_verified=False, classification=None,
                            classification_channel={"center_frequency_hz": latest_center, "sample_rate": latest_rate,
                                                    "bandwidth_hz": (2 * min(0.45 * latest_rate, 0.49 * sample_rate)
                                                                     if display_channel else sample_rate),
                                                    "selection_bandwidth_hz": display_channel["bandwidth"] if display_channel else sample_rate,
                                                    "samples_buffered": len(inference_iq),
                                                    "selection": "strongest_energy_channel" if strongest else "whole_window"},
                            active_channels=len(channels))
                        last_publish_rf = source_seconds
                    self._config["center_frequency_hz"] = center
                if quota_reached:
                    reason = "storage_limit"
                    break
                if not queued and receiver.paced:
                    self._stop.wait(max(0, len(iq) / sample_rate - (time.perf_counter() - read_start)))
            if self._stop.is_set():
                reason = "user_stop"
        except Exception as exc:
            reason = getattr(exc, "reason", "receiver_error")
            self._record_failure(exc)
        finally:
            self._reader_stop.set()
            if queued and self._reader_thread is not None:
                self._reader_thread.join(timeout=6)
                if self._reader_thread.is_alive():
                    self._record_failure(_CaptureFailure("Hardware-Lesethread reagiert nicht auf Stop; erneuter Start bleibt gesperrt.",
                                                         reason="reader_shutdown_timeout"))
            if self._error and reason not in {"queue_overflow", "receiver_overflow", "receiver_error", "writer_error", "invalid_samples", "sample_gap"}:
                reason = self._reader_reason or "receiver_error"
            if queued:
                discarded = 0
                while True:
                    try:
                        pending = self._capture_queue.get_nowait()
                    except queue.Empty:
                        break
                    discarded += len(pending[0])
                with self._lock:
                    self._queued_bytes = 0
                    self._performance["discarded_after_stop_samples"] = discarded
            for writer, source_stop in (([(wideband, wideband.samples + wideband.metadata["global"].get("sc:source_sample_start", 0))] if wideband else [])
                                         + [(c["writer"], c["source_stop"]) for c in channels]):
                try:
                    self._finish_writer(writer, reason, source_stop)
                except Exception as exc:
                    self._record_failure(_CaptureFailure(f"Aufnahme konnte nicht abgeschlossen werden: {exc}", reason="writer_error"))
                    reason = "writer_error"
            if not queued:
                try:
                    receiver.close()
                except Exception as exc:
                    self._record_failure(exc)
                    reason = "receiver_error"
            with self._lock:
                self._elapsed = time.monotonic() - started
                self._reason = reason
                self._state = "error" if self._error else ("stopped" if reason == "user_stop" else "completed")
