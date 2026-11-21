"""Conservative RF coverage planning and a single-receiver scan coordinator.

Plans describe sequential, requested windows, never simultaneous observation of
0–6 GHz. Timing comes from the host and sample counters, not RF timestamps or a
hardware acceptance test. Opening an actual SDR is delegated to CaptureManager.
"""
from __future__ import annotations

from collections import deque
import copy
from datetime import datetime, timezone
import math
import threading
import time
import uuid


_MAX_FREQUENCY = 6_000_000_000.0
_MAX_STEPS = 4096
_HISTORY_LIMIT = 128
_RECEIVERS = {
    "pluto": {"center_min_hz": 325_000_000.0, "center_max_hz": 3_800_000_000.0,
              "max_bandwidth_hz": 20_000_000.0, "sample_rate_min": 520_834.0, "sample_rate_max": 61_440_000.0},
    "rsp1a": {"center_min_hz": 1000.0, "center_max_hz": 2_000_000_000.0,
              "max_bandwidth_hz": 10_000_000.0, "sample_rate_min": 8000.0, "sample_rate_max": 10_000_000.0},
}


def _number(config, name, default, minimum, maximum):
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} muss eine Zahl sein.")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} muss zwischen {minimum:g} und {maximum:g} liegen.")
    return value


def _range(start, stop, **extra):
    return {"start_frequency_hz": float(start), "stop_frequency_hz": float(stop), **extra}


def _merge_ranges(ranges):
    merged = []
    for item in sorted(ranges, key=lambda value: value["start_frequency_hz"]):
        low, high = item["start_frequency_hz"], item["stop_frequency_hz"]
        if merged and low <= merged[-1]["stop_frequency_hz"] + 1e-6:
            merged[-1]["stop_frequency_hz"] = max(merged[-1]["stop_frequency_hz"], high)
        else:
            merged.append(_range(low, high))
    return merged


def build_scan_plan(config: dict) -> dict:
    """Plan bounded sequential windows and disclose unsupported intervals.

    Usable width is 80% of min(sample rate, specified analog RF bandwidth).
    Actual usable response/throughput remains unverified. Recording plans only
    describe their supplied, fixed window and cannot start an RF scan.
    """
    if not isinstance(config, dict):
        raise ValueError("Scan-Konfiguration muss ein JSON-Objekt sein.")
    receiver_config = config.get("receiver_config", {})
    if not isinstance(receiver_config, dict):
        raise ValueError("receiver_config muss ein JSON-Objekt sein.")
    source = config.get("source", "rsp1a")
    if source not in (*_RECEIVERS, "recording"):
        raise ValueError("Scan-Quelle muss rsp1a, pluto oder recording sein.")
    low = _number(config, "start_frequency_hz", 0, 0, _MAX_FREQUENCY)
    high = _number(config, "stop_frequency_hz", _MAX_FREQUENCY, 0, _MAX_FREQUENCY)
    if high <= low:
        raise ValueError("Die obere Scanfrequenz muss größer als die untere sein.")
    overlap = _number(config, "overlap_fraction", .2, 0, 1)
    if overlap == 1:
        raise ValueError("overlap_fraction muss kleiner als 1 sein.")
    dwell = _number(config, "dwell_seconds", 2, .1, 86_400)
    max_steps = config.get("max_steps", _MAX_STEPS)
    if type(max_steps) is not int or not 1 <= max_steps <= _MAX_STEPS:
        raise ValueError("max_steps muss eine ganze Zahl zwischen 1 und 4096 sein.")
    repeat = config.get("repeat", False)
    if type(repeat) is not bool:
        raise ValueError("repeat muss true oder false sein.")
    limits = copy.deepcopy(_RECEIVERS.get(source, {
        "center_min_hz": 0., "center_max_hz": _MAX_FREQUENCY,
        "max_bandwidth_hz": 61_440_000., "sample_rate_min": 8000., "sample_rate_max": 61_440_000.,
    }))
    rate = _number(config, "sample_rate", receiver_config.get("sample_rate", 2_000_000),
                   limits["sample_rate_min"], limits["sample_rate_max"])
    usable = .8 * min(rate, limits["max_bandwidth_hz"])
    steps = []
    notes = [
        "Ein Empfänger beobachtet jeweils nur ein Frequenzfenster. Alle übrigen Frequenzen bleiben währenddessen unbeobachtet.",
        "Abdeckung ist eine Planungsannahme aus spezifizierten RF-Grenzen und 80 % nutzbarer Bandbreite, kein gemessener Empfangsnachweis.",
        "Abstimmen, Treiberstart/-stop und Analyse erzeugen zusätzliche Beobachtungslücken; die Mindestdauer enthält nur angeforderte Verweilzeiten.",
        "0 Hz/DC ist mit diesen Live-Empfängern nicht als untere garantierte RF-Grenze erreichbar; außerhalb der Hardwaregrenzen findet kein Scan statt.",
    ]
    if source == "recording":
        if "center_frequency_hz" not in config and "center_frequency_hz" not in receiver_config:
            raise ValueError("Recording-Plan benötigt die feste center_frequency_hz der Aufnahme.")
        if "sample_rate" not in config and "sample_rate" not in receiver_config:
            raise ValueError("Recording-Plan benötigt die tatsächliche sample_rate der Aufnahme.")
        center = _number(config, "center_frequency_hz", receiver_config.get("center_frequency_hz"), 0, _MAX_FREQUENCY)
        start, stop = max(0., center - usable / 2), min(_MAX_FREQUENCY, center + usable / 2)
        if min(stop, high) > max(start, low):
            steps.append({"index": 0, "center_frequency_hz": center, **_range(start, stop)})
        notes.append("Eine Aufnahme enthält nur ihr angegebenes festes IQ-Fenster; ein weiterer Frequenzbereich wird nicht durch Replay ergänzt.")
    else:
        start, stop = max(low, limits["center_min_hz"]), min(high, limits["center_max_hz"])
        if stop > start:
            width = stop - start
            stride = usable * (1 - overlap)
            count = 1 if width <= usable else int(math.ceil((width - usable) / stride)) + 1
            if count > max_steps:
                raise ValueError(f"Scan benötigt {count} Fenster und überschreitet max_steps={max_steps}. Abtastrate/Bandbreite erhöhen oder Scanbereich eingrenzen.")
            for index in range(count):
                center = ((start + stop) / 2 if count == 1 else
                          min(start + usable / 2 + index * stride, stop - usable / 2))
                center = min(limits["center_max_hz"], max(limits["center_min_hz"], center))
                steps.append({"index": index, "center_frequency_hz": center,
                              **_range(max(limits["center_min_hz"], center - usable / 2),
                                       min(limits["center_max_hz"], center + usable / 2))})
    covered = _merge_ranges([_range(max(low, step["start_frequency_hz"]), min(high, step["stop_frequency_hz"])) for step in steps])
    uncovered, cursor = [], low
    for interval in covered:
        if interval["start_frequency_hz"] > cursor:
            uncovered.append(_range(cursor, interval["start_frequency_hz"]))
        cursor = max(cursor, interval["stop_frequency_hz"])
    if cursor < high:
        uncovered.append(_range(cursor, high))
    covered_width = sum(interval["stop_frequency_hz"] - interval["start_frequency_hz"] for interval in covered)
    return {"schema_version": 1, "source": source, "requested_range": _range(low, high),
            "receiver_limits": {key: value for key, value in limits.items() if key in ("center_min_hz", "center_max_hz", "max_bandwidth_hz")},
            "receiver_config": copy.deepcopy(receiver_config), "sample_rate": rate, "usable_bandwidth_hz": usable,
            "overlap_fraction": overlap, "dwell_seconds": dwell, "repeat": repeat, "max_steps": max_steps,
            "steps": steps, "step_count": len(steps), "covered_ranges": covered, "uncovered_ranges": uncovered,
            "coverage_fraction": min(1., covered_width / (high - low)),
            "estimated_min_sweep_seconds": len(steps) * dwell, "simultaneous": False,
            "coverage_verified": False, "hardware_verified": False, "notes": notes}


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _busy(status):
    return bool(status.get("busy") or status.get("running") or status.get("state") in ("starting", "running"))


class ScanController:
    """Bounded thread coordinator for one CaptureManager.

    App entry points must serialize direct receiver changes against scan changes.
    The interface has no atomic stop-if-session operation; session IDs are checked
    before every stop and a different/already ended session is never stopped.
    on_window() runs synchronously after stop, while that window's buffers remain.
    Its time is explicitly an analysis pause, never an RF observation interval.
    """

    def __init__(self, capture, on_window=None):
        if on_window is not None and not callable(on_window):
            raise ValueError("on_window muss aufrufbar sein.")
        self.capture = capture
        self.on_window = on_window
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._owned_session_id = None
        self._windows = deque(maxlen=_HISTORY_LIMIT)
        self._gaps = deque(maxlen=_HISTORY_LIMIT)
        self._last_progress_monotonic = None
        self._last_window_end_monotonic = None
        self._last_window_end_at = None
        self._poll_seconds = .05
        self._join_seconds = 8.
        self._state = self._empty_state()

    @staticmethod
    def _empty_state():
        return {"state": "stopped", "running": False, "scan_id": None, "plan": None,
                "current_step": None, "current_cycle": 0, "completed_steps": 0, "completed_cycles": 0,
                "started_at": None, "ended_at": None, "error": None, "stop_reason": None,
                "last_sample_progress_at": None, "total_tuning_seconds": 0., "total_stop_seconds": 0.,
                "total_analysis_pause_seconds": 0., "total_inter_window_gap_seconds": 0.,
                "history_dropped_windows": 0, "history_limit": _HISTORY_LIMIT,
                "simultaneous": False, "coverage_verified": False, "hardware_verified": False,
                "timing_source": "host_monotonic_and_utc; sample duration uses processed counters/reported rate, not verified RF timestamps",
                "note": "Sequentieller Scan; Planabdeckung und Host-Verweilzeit sind kein Nachweis lückenlosen oder kalibrierten RF-Empfangs."}

    def start(self, config: dict) -> dict:
        plan = build_scan_plan(config)
        if plan["source"] not in _RECEIVERS:
            raise ValueError("Ein Live-Scan benötigt PlutoSDR oder RSP1A; Recording bleibt ein festes Frequenzfenster.")
        if not plan["steps"]:
            raise ValueError("Kein Scanfenster liegt im spezifizierten Frequenzbereich des gewählten Empfängers.")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Ein Scan läuft bereits oder wird noch beendet.")
            if _busy(self.capture.status()):
                raise ValueError("Der Empfänger ist belegt. Bestehenden Empfang zuerst selbst beenden; der Scan stoppt keine fremde Sitzung.")
            self._windows.clear()
            self._gaps.clear()
            self._last_progress_monotonic = None
            self._last_window_end_monotonic = None
            self._last_window_end_at = None
            self._owned_session_id = None
            self._state = self._empty_state() | {"state": "starting", "running": True,
                                                "scan_id": uuid.uuid4().hex, "plan": plan, "started_at": _utc()}
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="signalclassifier-scan", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            thread = self._thread
            if thread is not None and thread.is_alive():
                self._state.update(state="stopping", stop_reason="user_stop")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._join_seconds)
        return self.status()

    def status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            result = copy.deepcopy(self._state)
            result["running"] = self._thread is not None and self._thread.is_alive()
            result["owned_session_id"] = self._owned_session_id
            age = max(0., now - self._last_progress_monotonic) if self._last_progress_monotonic is not None else None
            result["sample_progress_age_seconds"] = age
            stale_after = max(1., (self._state["plan"] or {}).get("dwell_seconds", 2.) * 2)
            result["data_stale"] = age is None or age > stale_after
            result["stale_after_seconds"] = stale_after
            result["observed_windows"] = []
            for window in self._windows:
                public = {key: copy.deepcopy(value) for key, value in window.items() if not key.startswith("_")}
                progress_time = window.get("_last_progress_monotonic")
                public["sample_progress_age_seconds"] = max(0., now - progress_time) if progress_time is not None else None
                public["data_stale"] = progress_time is None or now - progress_time > stale_after
                result["observed_windows"].append(public)
            result["observation_gaps"] = copy.deepcopy(list(self._gaps))
            return result

    def _session_status(self):
        status = self.capture.status()
        with self._lock:
            owned = self._owned_session_id
        if not owned or status.get("session_id") != owned:
            with self._lock:
                self._owned_session_id = None
            raise RuntimeError("Empfangssitzung wurde außerhalb des Scans geändert; fremde Sitzung bleibt unangetastet.")
        return status

    def _stop_owned(self):
        status = self._session_status()
        if _busy(status):
            status = self.capture.stop()
            if status.get("session_id") != self._owned_session_id:
                with self._lock:
                    self._owned_session_id = None
                raise RuntimeError("Empfangssitzung änderte sich während des Scan-Stops.")
            if _busy(status):
                raise RuntimeError("Scan-Empfänger reagiert nicht auf Stop; weiterer Scan bleibt bis zum Threadende gesperrt.")
        return status

    def _observe_progress(self, status, previous_samples):
        samples = status.get("samples")
        if isinstance(samples, (int, float)) and not isinstance(samples, bool) and math.isfinite(samples) and samples > previous_samples:
            now = time.monotonic()
            with self._lock:
                self._last_progress_monotonic = now
                self._state["last_sample_progress_at"] = _utc()
            return int(samples), now
        return previous_samples, None

    def _capture_step(self, step, cycle):
        with self._lock:
            plan = self._state["plan"]
            self._state.update(state="running", current_step=copy.deepcopy(step), current_cycle=cycle)
        if _busy(self.capture.status()):
            raise RuntimeError("Empfänger wurde vor dem nächsten Scanfenster belegt; fremde Sitzung bleibt unangetastet.")
        config = copy.deepcopy(plan["receiver_config"])
        config.update(source=plan["source"], sample_rate=plan["sample_rate"], center_frequency_hz=step["center_frequency_hz"],
                      duration_seconds=plan["dwell_seconds"], scan_id=self._state["scan_id"],
                      capture_group_id="scan-" + self._state["scan_id"])
        config.setdefault("mode", "monitor")
        tune_start, tune_at = time.monotonic(), _utc()
        status = self.capture.start(config)
        tune_end, dwell_at = time.monotonic(), _utc()
        session = status.get("session_id")
        if not isinstance(session, str) or not session:
            raise RuntimeError("Empfängerstart lieferte keine session_id; kein unsicherer Stop ohne Sitzungseigentum.")
        with self._lock:
            self._owned_session_id = session
            self._state["total_tuning_seconds"] += max(0., tune_end - tune_start)
            if self._last_window_end_monotonic is not None:
                seconds = max(0., tune_end - self._last_window_end_monotonic)
                self._gaps.append({"started_at": self._last_window_end_at, "ended_at": dwell_at, "seconds": seconds,
                                   "scope": "Host-Koordination zwischen angeforderten Fenstern einschließlich Stop, Analyse und Start; RF-Lücke nicht hardwareverifiziert."})
                self._state["total_inter_window_gap_seconds"] += seconds
        deadline = tune_end + plan["dwell_seconds"]
        samples, last_progress = self._observe_progress(status, 0)
        while not self._stop.is_set():
            status = self._session_status()
            samples, progress_time = self._observe_progress(status, samples)
            last_progress = progress_time if progress_time is not None else last_progress
            if status.get("error") or status.get("state") == "error":
                break
            if not _busy(status) or time.monotonic() >= deadline:
                break
            self._stop.wait(min(self._poll_seconds, max(0., deadline - time.monotonic())))
        dwell_end, dwell_end_at = time.monotonic(), _utc()
        stop_start = time.monotonic()
        status = self._stop_owned()
        stop_seconds = max(0., time.monotonic() - stop_start)
        samples, progress_time = self._observe_progress(status, samples)
        last_progress = progress_time if progress_time is not None else last_progress
        actual_rate = status.get("sample_rate") or status.get("config", {}).get("sample_rate")
        if isinstance(actual_rate, bool) or not isinstance(actual_rate, (int, float)) or not math.isfinite(actual_rate) or actual_rate <= 0:
            actual_rate = None
        sample_duration = samples / actual_rate if actual_rate else None
        window = {**copy.deepcopy(step), "cycle": cycle, "session_id": session,
                  "source": plan["source"], "requested_dwell_seconds": plan["dwell_seconds"],
                  "requested_sample_rate": plan["sample_rate"], "reported_sample_rate": actual_rate,
                  "reported_center_frequency_hz": status.get("center_frequency_hz"),
                  "tuning_requested_at": tune_at, "host_dwell_started_at": dwell_at, "host_dwell_ended_at": dwell_end_at,
                  "capture_started_at": status.get("started_at"), "tuning_seconds": max(0., tune_end - tune_start),
                  "host_dwell_seconds": max(0., dwell_end - tune_end), "stop_seconds": stop_seconds,
                  "sample_count": samples, "sample_duration_seconds": sample_duration, "has_sample_evidence": samples > 0,
                  "sample_duration_scope": "Verarbeitete Samples / gemeldete Abtastrate; keine verifizierte zusammenhängende RF-Zeit oder UTC-Samplezeit.",
                  "capture_reason": status.get("reason"), "capture_error": status.get("error"),
                  "recordings_saved": list(status.get("recordings_saved", [])), "analysis_pause_seconds": 0.,
                  "coverage_verified": False, "hardware_verified": False, "rf_timestamp_verified": False,
                  "_last_progress_monotonic": last_progress}
        with self._lock:
            self._last_window_end_monotonic = dwell_end
            self._last_window_end_at = dwell_end_at
            self._state["total_stop_seconds"] += stop_seconds
            self._state["completed_steps"] += 1
            if len(self._windows) == self._windows.maxlen:
                self._state["history_dropped_windows"] += 1
            self._windows.append(window)
        if status.get("error") or status.get("state") == "error":
            raise RuntimeError(status.get("error") or "Empfänger meldet einen Fehler.")
        if self.on_window is not None and not self._stop.is_set():
            # No subsequent capture starts until the synchronous handoff has
            # consumed this window's final buffers. This is an explicit pause.
            analysis_start = time.monotonic()
            with self._lock:
                self._state["state"] = "analyzing"
            try:
                self.on_window()
            except Exception as exc:
                with self._lock:
                    window["analysis_error"] = str(exc)
                raise
            finally:
                duration = max(0., time.monotonic() - analysis_start)
                with self._lock:
                    window["analysis_pause_seconds"] = duration
                    self._state["total_analysis_pause_seconds"] += duration
        with self._lock:
            self._owned_session_id = None

    def _run(self):
        error = None
        try:
            cycle = 0
            while not self._stop.is_set():
                cycle += 1
                with self._lock:
                    steps = self._state["plan"]["steps"]
                    repeat = self._state["plan"]["repeat"]
                for step in steps:
                    if self._stop.is_set():
                        break
                    self._capture_step(step, cycle)
                if self._stop.is_set():
                    break
                with self._lock:
                    self._state["completed_cycles"] += 1
                if not repeat:
                    break
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            with self._lock:
                owned = self._owned_session_id
            if owned:
                try:
                    self._stop_owned()
                except Exception as exc:
                    error = error or str(exc) or type(exc).__name__
            with self._lock:
                self._owned_session_id = None
                self._state.update(state="error" if error else "stopped" if self._stop.is_set() else "completed",
                                   running=False, ended_at=_utc(), error=error,
                                   stop_reason="error" if error else "user_stop" if self._stop.is_set() else "plan_complete")
