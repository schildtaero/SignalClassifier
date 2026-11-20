"""Explicit live/replay receivers, spectral detection and bounded IQ capture."""
from __future__ import annotations

import importlib
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal

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
            raise RuntimeError(f"SoapySDR-Streamfehler {response.ret}; eine Datenlücke wird nicht als kontinuierliche Aufnahme gespeichert.")
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
        self.taps = signal.firwin(taps, cutoff=min(0.45 * self.output_rate, 0.49 * input_rate), fs=input_rate)
        self.tail = np.zeros(len(self.taps) - 1, np.complex64)
        self.position = 0

    def process(self, iq: np.ndarray) -> np.ndarray:
        indices = np.arange(len(iq), dtype=np.float64) + self.position
        mixed = iq * np.exp(-2j * np.pi * (self.offset / self.input_rate) * indices)
        joined = np.concatenate((self.tail, mixed))
        filtered = signal.fftconvolve(joined, self.taps, mode="valid")
        start = (-self.position) % self.decimation
        output = filtered[start::self.decimation]
        self.tail = joined[-len(self.tail):].astype(np.complex64)
        self.position += len(iq)
        return output.astype(np.complex64)


class CaptureManager:
    """One explicit receiver, one acquisition thread, bounded capture storage."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.recordings_dir = self.data_dir / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
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

    def start(self, config: dict) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Ein Empfänger läuft bereits; zuerst stoppen.")
            config = validate_config(config)
            self._error = None
            self._reason = None
            self._config = config
            self._state = "starting"
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
        extra = {"sc:stop_reason": reason}
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
            written = writer.append(segment)
            with self._lock:
                self._bytes += written
        return len(segment) == len(iq)

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
        last_publish = 0.0
        reason = "user_stop"
        try:
            if self._config["mode"] == "wideband":
                wideband = self._new_writer("wideband", receiver.sample_rate, receiver.center_frequency)
            while not self._stop.is_set():
                if time.monotonic() - started >= self._config["duration_seconds"]:
                    reason = "duration_limit"
                    break
                if self._config["mode"] != "monitor" and self._bytes >= self._config["max_bytes"]:
                    reason = "storage_limit"
                    break
                block_start = time.monotonic()
                iq = receiver.read(self._config["block_size"])
                if iq is None:
                    continue
                if not len(iq):
                    reason = "end_of_recording"
                    break
                if not np.isfinite(iq).all():
                    raise RuntimeError("Empfänger lieferte NaN oder unendliche IQ-Werte.")
                remaining = max(0, int(self._config["duration_seconds"] * receiver.sample_rate) - position)
                iq = np.asarray(iq[:remaining], np.complex64)
                if not len(iq):
                    reason = "duration_limit"
                    break
                center = receiver.center_frequency
                if center != last_center:
                    # Replay retunes are explicitly represented in the capture
                    # metadata, never joined as if frequency were unchanged.
                    if wideband:
                        wideband.metadata["captures"].append({"core:sample_start": wideband.samples, "core:frequency": center})
                    for channel in channels:
                        self._finish_writer(channel["writer"], "retune", position)
                    channels = []
                    display_channel = None
                    last_center = center
                analysis = spectrum_and_detections(iq, receiver.sample_rate, center, self._config["threshold_db"],
                                                   merge_gap_hz=self._config["merge_gap_hz"])
                if wideband and not self._append_bounded(wideband, iq):
                    position += len(iq)
                    with self._lock:
                        self._samples = position
                    reason = "storage_limit"
                    break
                if self._config["mode"] == "energy":
                    # Keep at most eight simultaneous channel writers. Short
                    # dropouts retain 0.25 s post-trigger data; there is no
                    # retrospective pretrigger buffer in this build.
                    candidates = analysis["detections"][:8]
                    matched = set()
                    for detection in candidates:
                        frequency = detection["frequency_hz"]
                        bin_width = receiver.sample_rate / analysis["fft_size"]
                        bandwidth = min(receiver.sample_rate * 0.9,
                                        max(self._config["minimum_channel_bandwidth_hz"], 8 * bin_width, detection["bandwidth_hz"] * 1.5))
                        existing = next((c for c in channels if id(c) not in matched
                                         and abs(c["frequency"] - frequency) < max(c["bandwidth"] / 2, 4 * bin_width)), None)
                        if existing is not None and bandwidth > existing["bandwidth"] * 1.5:
                            self._finish_writer(existing["writer"], "channel_bandwidth_changed", position)
                            channels.remove(existing)
                            existing = None
                        if existing is None:
                            if len(channels) >= 8:
                                continue
                            channel_counter += 1
                            extractor = _Channelizer(receiver.sample_rate, frequency - center, bandwidth)
                            writer = self._new_writer(f"channel-{channel_counter:04d}", extractor.output_rate, frequency,
                                {"sc:parent_center_frequency_hz": center,
                                 "sc:parent_sample_rate": receiver.sample_rate,
                                 "sc:source_sample_start": position,
                                 "sc:selected_bandwidth_hz": bandwidth,
                                 "sc:decimation": extractor.decimation,
                                 "sc:fir_group_delay_input_samples": (len(extractor.taps) - 1) / 2,
                                 "sc:detection_threshold_db": self._config["threshold_db"],
                                 "sc:detection_method": "median_bin_energy",
                                 "sc:pretrigger_seconds": 0,
                                 "sc:posttrigger_seconds": 0.25})
                            existing = {"frequency": frequency, "bandwidth": bandwidth, "extractor": extractor,
                                        "writer": writer, "last_seen": position}
                            channels.append(existing)
                        existing["last_seen"] = position + len(iq)
                        matched.add(id(existing))
                    for channel in list(channels):
                        output = channel["extractor"].process(iq)
                        if not self._append_bounded(channel["writer"], output):
                            reason = "storage_limit"
                            self._stop.set()
                        if position + len(iq) - channel["last_seen"] > receiver.sample_rate * 0.25:
                            self._finish_writer(channel["writer"], "energy_gate_closed", position + len(iq))
                            channels.remove(channel)
                # Polling/classification receives a channelized strongest signal,
                # rather than conflating multiple emitters in the whole RF window.
                strongest = analysis["detections"][0] if analysis["detections"] else None
                if strongest:
                    bandwidth = min(receiver.sample_rate * 0.9, max(self._config["minimum_channel_bandwidth_hz"], receiver.sample_rate / analysis["fft_size"] * 8,
                                     strongest["bandwidth_hz"] * 1.5))
                    if (display_channel is None or abs(display_channel["frequency"] - strongest["frequency_hz"]) > display_channel["bandwidth"] / 2
                            or display_channel["extractor"].input_rate != receiver.sample_rate
                            or bandwidth > display_channel["bandwidth"] * 1.5):
                        display_channel = {"frequency": strongest["frequency_hz"], "bandwidth": bandwidth,
                                           "extractor": _Channelizer(receiver.sample_rate, strongest["frequency_hz"] - center, bandwidth)}
                    latest_iq = display_channel["extractor"].process(iq)
                    latest_rate = display_channel["extractor"].output_rate
                    latest_center = display_channel["frequency"]
                else:
                    display_channel = None
                    latest_iq, latest_rate, latest_center = iq, receiver.sample_rate, center
                # Neural/model windows may be longer than one decimated receiver
                # block. Accumulate only consecutive samples from the identical
                # extractor context, retaining its continuous FIR/decimation state.
                # Holding the extractor object in this tuple also prevents an
                # object-id reuse from accidentally matching an older channel.
                context = (display_channel["extractor"] if display_channel else None,
                           latest_rate, latest_center)
                if context != inference_context:
                    inference_iq = np.empty(0, np.complex64)
                    inference_context = context
                if len(latest_iq) >= 65536:
                    inference_iq = latest_iq[-65536:].copy()
                else:
                    inference_iq = np.concatenate((inference_iq, latest_iq))[-65536:]
                position += len(iq)
                with self._lock:
                    self.latest_iq = inference_iq.copy()
                    self.latest_sample_rate = latest_rate
                    self.latest_center_frequency = latest_center
                    self._samples = position
                    self._elapsed = time.monotonic() - started
                    analysis.update({"sample_rate": receiver.sample_rate, "center_frequency_hz": center,
                                     "source": self._config["source"], "mode": self._config["mode"],
                                     "synthetic": self._config["source"] == "synthetic", "sample_index": position - len(iq),
                                     "timestamp": datetime.now(timezone.utc).isoformat(), "classification": None,
                                     "classification_channel": {"center_frequency_hz": latest_center, "sample_rate": latest_rate,
                                                                "samples_buffered": len(inference_iq),
                                                                "selection": "strongest_energy_channel" if strongest else "whole_window"},
                                     "active_channels": len(channels)})
                    self._snapshot = analysis
                    self._config["center_frequency_hz"] = center
                if receiver.paced:
                    self._stop.wait(max(0, len(iq) / receiver.sample_rate - (time.monotonic() - block_start)))
            if self._stop.is_set() and reason != "storage_limit":
                reason = "user_stop"
        except Exception as exc:
            reason = "receiver_error"
            with self._lock:
                self._error = str(exc)
                self._state = "error"
        finally:
            for writer in ([wideband] if wideband else []) + [c["writer"] for c in channels]:
                try:
                    self._finish_writer(writer, reason, writer.samples if writer is wideband else position)
                except Exception as exc:
                    with self._lock:
                        self._error = f"Aufnahme konnte nicht abgeschlossen werden: {exc}"
                        self._state = "error"
            try:
                receiver.close()
            except Exception as exc:
                with self._lock:
                    self._error = f"Empfänger konnte nicht sauber geschlossen werden: {exc}"
                    self._state = "error"
            with self._lock:
                self._elapsed = time.monotonic() - started
                self._reason = reason
                if self._state != "error":
                    self._state = "stopped" if reason == "user_stop" else "completed"
