"""Bounded multi-signal observations and persistent, inspectable decisions.

Acquisition never waits for a neural model. This worker consumes channel
snapshots, keeps unknown/budget states visible, and stores no raw IQ in SQLite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from . import __version__, decision
from .db import utcnow

LOG = logging.getLogger(__name__)
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
DEFAULT_SETTINGS = {"enabled": True, "start_frequency_hz": 0., "stop_frequency_hz": 6e9,
                    "interval_seconds": .5, "max_signals_per_cycle": 4,
                    "use_priors": False, "use_active_model": True,
                    "max_history": 5000, "max_signals": 2000,
                    "policy": {"min_model_score": .65, "min_margin": .15,
                               "budget_ms": 500, "max_models": 3, "require_evaluated_models": True},
                    "experts": []}


def _number(value, key, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key}: Zahl zwischen {low:g} und {high:g} erforderlich")
    if integer and type(value) is not int:
        raise ValueError(f"{key}: ganze Zahl erforderlich")
    return int(value) if integer else float(value)


def normalize_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError("Unbekannte Felder oder ungültige Analysekonfiguration")
    result = {**DEFAULT_SETTINGS, **value}
    for key in ("enabled", "use_priors", "use_active_model"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key}: true oder false erforderlich")
    for key in ("start_frequency_hz", "stop_frequency_hz"):
        result[key] = _number(result[key], key, 0, 6e9)
    if result["start_frequency_hz"] >= result["stop_frequency_hz"]:
        raise ValueError("Der überwachte Frequenzbereich muss eine positive Breite haben")
    result["interval_seconds"] = _number(result["interval_seconds"], "interval_seconds", .1, 30)
    for key, low, high in (("max_signals_per_cycle", 1, 16), ("max_history", 100, 100000), ("max_signals", 100, 10000)):
        result[key] = _number(result[key], key, low, high, True)
    result["policy"] = decision.normalize_policy(result["policy"])
    experts = result["experts"]
    if not isinstance(experts, list) or len(experts) > 64:
        raise ValueError("Höchstens 64 registrierte Spezialmodelle zulässig")
    normalized, identifiers = [], set()
    for item in experts:
        if not isinstance(item, dict) or set(item) - {"model_id", "enabled", "level", "families"}:
            raise ValueError("Ungültiger Spezialmodelleintrag")
        identifier = item.get("model_id")
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or ".." in identifier or identifier in identifiers:
            raise ValueError("Spezialmodell benötigt eine eindeutige Modellkennung")
        identifiers.add(identifier)
        enabled = item.get("enabled", True)
        level = item.get("level", "modulation")
        families = item.get("families", [])
        if type(enabled) is not bool or level not in {"waveform_family", "modulation", "protocol", "service", "emitter"}:
            raise ValueError("Ungültige Aktivierung oder Aussageebene des Spezialmodells")
        if (not isinstance(families, list) or len(families) > 32
                or not all(isinstance(f, str) and 0 < len(f) <= 80 for f in families)):
            raise ValueError("Spezialmodellfamilien müssen eine begrenzte Liste von Kennungen sein")
        normalized.append({"model_id": identifier, "enabled": enabled, "level": level, "families": list(dict.fromkeys(families))})
    result["experts"] = normalized
    return result


def _safe(value):
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items() if k != "iq"}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _safe(value.tolist())
    if isinstance(value, np.generic):
        return _safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def _context_revision(entry):
    """Metadata can change a decision even when its IQ window is unchanged."""
    return hashlib.sha256(json.dumps(_safe(entry), sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


class SignalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL, session_id TEXT)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
            if "session_id" not in columns:
                # DDL and the one-time payload backfill must commit together:
                # otherwise a crash after ALTER could leave old rows invisible
                # to session queries on the next application start.
                conn.execute("BEGIN IMMEDIATE")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
                if "session_id" not in columns:
                    conn.execute("ALTER TABLE signals ADD COLUMN session_id TEXT")
                    cursor = conn.execute("SELECT id,payload FROM signals")
                    while rows := cursor.fetchmany(32):
                        updates = []
                        for identifier, payload in rows:
                            try:
                                value = json.loads(payload)
                                session = value.get("session_id") if isinstance(value, dict) else None
                            except (ValueError, TypeError):
                                session = None
                            updates.append((session if isinstance(session, str) else None, identifier))
                        conn.executemany("UPDATE signals SET session_id=? WHERE id=?", updates)
            conn.execute("CREATE INDEX IF NOT EXISTS signals_session_updated_id ON signals(session_id,updated_at DESC,id)")
            conn.execute("CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, signal_id TEXT NOT NULL, payload TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS observations_signal ON observations(signal_id,seq)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, signal, observation_id=None, max_history=5000, max_signals=2000):
        signal = _safe(signal)
        encoded = json.dumps(signal, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 512 * 1024:
            raise ValueError("Entscheidungsprotokoll überschreitet 512 KiB; keine IQ-Daten im Protokoll zulässig")
        with self.lock, self.connect() as conn:
            if observation_id:
                old = conn.execute("SELECT payload FROM observations WHERE id=?", (observation_id,)).fetchone()
                if old:
                    return json.loads(old[0])
            conn.execute("INSERT INTO signals(id,updated_at,payload,session_id) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload,session_id=excluded.session_id",
                         (signal["id"], signal.get("analysis_at", signal["last_seen"]), encoded,
                          signal.get("session_id") if isinstance(signal.get("session_id"), str) else None))
            if observation_id:
                conn.execute("INSERT OR IGNORE INTO observations(id,signal_id,payload) VALUES(?,?,?)", (observation_id, signal["id"], encoded))
            removed = conn.execute("DELETE FROM observations WHERE seq NOT IN (SELECT seq FROM observations ORDER BY seq DESC LIMIT ?)", (max_history,)).rowcount
            removed_signals = conn.execute("DELETE FROM signals WHERE id NOT IN (SELECT id FROM signals ORDER BY updated_at DESC,id LIMIT ?)", (max_signals,)).rowcount
            removed += conn.execute("DELETE FROM observations WHERE signal_id NOT IN (SELECT id FROM signals)").rowcount
            for key, count in (("history_pruned", removed), ("signals_pruned", removed_signals)):
                if count:
                    conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value", (key, count))
        return signal

    def get(self, identifier):
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT payload FROM signals WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit=100, session_id=None, compact=False):
        limit = max(1, min(2000, int(limit)))
        with self.lock, self.connect() as conn:
            if session_id is None:
                rows = conn.execute("SELECT payload FROM signals ORDER BY updated_at DESC,id LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute("SELECT payload FROM signals WHERE session_id=? ORDER BY updated_at DESC,id LIMIT ?", (session_id, limit)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        if compact:
            for value in values:
                value.pop("analysis_settings", None)
                value.pop("source_context", None)
                full = value["decision"]
                value["decision"] = {k: full[k] for k in ("schema_version", "label", "status", "conclusions", "elapsed_ms", "budget", "warnings") if k in full}
        return values

    def history(self, identifier, limit=50):
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT payload FROM observations WHERE signal_id=? ORDER BY seq DESC LIMIT ?", (identifier, max(1, min(500, int(limit))))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def stats(self):
        with self.lock, self.connect() as conn:
            result = {key: value for key, value in conn.execute("SELECT key,value FROM meta")}
            result.update(signal_count=conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
                          observation_count=conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        return {"history_pruned": 0, "signals_pruned": 0, **result}


class AnalysisManager:
    def __init__(self, capture, settings_store, data_dir, resolve_model, active_model, prior_lookup, *, start_worker=True):
        self.capture, self.settings_store = capture, settings_store
        self.resolve_model, self.active_model, self.prior_lookup = resolve_model, active_model, prior_lookup
        self.signals = SignalStore(Path(data_dir) / "signals.sqlite3")
        self.lock = threading.RLock()
        self.cycle_lock = threading.RLock()
        self.stop_event = threading.Event()
        saved = settings_store.get("analysis_settings", "main") or {}
        saved.pop("id", None)
        self.settings = normalize_settings(saved)
        self.revision = 0
        self.cursor = 0
        self.seen = {}
        self.pipeline_fingerprint = {"application": "SignalClassifier", "application_version": __version__,
            "source_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
                ("decision.py", Path(decision.__file__)), ("analysis_runtime.py", Path(__file__)))},
            "source_scope": "Backend source bytes at analysis worker startup; model artifacts are hashed separately."}
        self.stats = {"cycles": 0, "analyzed_observations": 0, "pending_signals": 0,
                      "unbuffered_detections": 0, "last_cycle_ms": 0., "last_error": None, "paused_reason": None}
        self.thread = None
        if start_worker:
            self.thread = threading.Thread(target=self._run, name="signal-analysis", daemon=True)
            self.thread.start()

    def get_settings(self):
        with self.lock:
            return json.loads(json.dumps(self.settings))

    def classification_enabled(self):
        return self.capture.status().get("config", {}).get("classification_enabled", True) is not False

    def set_settings(self, value):
        # Resolve all identifiers before persisting: API clients cannot supply paths.
        value = normalize_settings(value)
        for expert in value["experts"]:
            self.resolve_model(expert["model_id"])
        with self.lock:
            self.settings_store.put("analysis_settings", "main", value)
            self.settings = value
            self.revision += 1
            self.seen.clear()
        return self.get_settings()

    def _models(self, settings):
        profiles = list(settings["experts"])
        active = self.active_model() if settings["use_active_model"] else None
        if active and active not in {p["model_id"] for p in profiles}:
            profiles.append({"model_id": active, "enabled": True, "level": "modulation", "families": []})
        result = []
        for p in profiles:
            if not p["enabled"]:
                continue
            try:
                value = self.resolve_model(p["model_id"])
                result.append({"id": p["model_id"], "name": value.get("name", p["model_id"]),
                               "model_path": value["model_path"], "level": p["level"], "families": p["families"]})
            except Exception as exc:
                # Keep unavailable experts in the trace instead of silently deleting them.
                result.append({"id": p["model_id"], "name": p["model_id"], "model_path": None,
                               "level": p["level"], "families": p["families"], "unavailable_reason": str(exc)})
        return result

    @staticmethod
    def _model_revision(models):
        """Cheap revision marker; exact executed artifact hashes live in traces."""
        signatures = []
        for model in models:
            root = Path(model.get("model_path") or "")
            files = []
            if model.get("model_path"):
                for name in ("model.json", "weights.pt", "weights.npz", "normalization.npz"):
                    try:
                        stat = (root / name).stat()
                        files.append((name, stat.st_mtime_ns, stat.st_size, stat.st_ino))
                    except OSError:
                        files.append((name, None))
            signatures.append({"id": model["id"], "level": model.get("level"),
                               "families": model.get("families", []), "files": files})
        return hashlib.sha256(json.dumps(signatures, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _signal_id(entry):
        key = f"{entry.get('session_id') or entry.get('source_epoch')}|{entry.get('channel_id')}|{entry.get('source_epoch')}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def _record(self, entry, result, observation_id=None, settings=None):
        identifier = self._signal_id(entry)
        previous = self.signals.get(identifier)
        now = entry.get("observed_at") or utcnow()
        value = {"id": identifier, "session_id": entry.get("session_id") or entry.get("source_epoch"),
                 "channel_id": entry.get("channel_id"), "source_epoch": entry.get("source_epoch"),
                 "origin_group_id": entry.get("origin_group_id"), "source": entry.get("source"),
                 "synthetic": entry.get("synthetic", entry.get("source") == "synthetic"), "recording_id": entry.get("recording_id"),
                 "first_seen": previous["first_seen"] if previous else now, "last_seen": now,
                 "center_frequency_hz": entry.get("center_frequency_hz"), "bandwidth_hz": entry.get("bandwidth_hz"),
                 "sample_rate": entry.get("sample_rate"), "sample_count": entry.get("sample_count", len(entry.get("iq", []))),
                 "observation_count": (previous or {}).get("observation_count", 0) + (1 if observation_id else 0),
                 "status": result["status"], "decision": result, "analysis_at": utcnow(),
                 "analysis_settings": settings or self.get_settings(), "observation_id": entry.get("observation_id"),
                 "source_context": _safe({k: entry[k] for k in (
                     "source_sample_start", "source_sample_stop", "source_sample_stride", "source_block_stop",
                     "parent_sample_rate", "parent_center_frequency_hz", "source_index_scope",
                     "fir_group_delay_input_samples", "detection_source_sample_stop", "detection", "quality",
                     "data_provenance", "decoder_evidence", "timestamp_source", "rf_timestamp_verified", "observed_at",
                     "recording_id", "sample_count", "duration_seconds", "selection_bandwidth_hz", "scope") if k in entry}),
                 "activity": "ended" if entry.get("ended_at") else "observed",
                 "observation_type": entry.get("observation_type", "detected_channel"),
                 **{k: entry[k] for k in ("ended_at", "end_reason", "iq_retained") if k in entry}}
        settings = settings or self.get_settings()
        return self.signals.save(value, observation_id, settings["max_history"], settings["max_signals"])

    def analyze_entry(self, entry, *, models=None, settings=None):
        settings = settings or self.get_settings()
        context = _safe({k: v for k, v in entry.items() if k != "iq"})
        hints = self.prior_lookup(entry.get("center_frequency_hz")) if settings["use_priors"] and entry.get("center_frequency_hz") is not None else []
        hint_count = len(hints)
        resolved_models = self._models(settings) if models is None else models
        result = decision.analyze_signal(entry.get("iq", np.empty(0, np.complex64)), entry.get("sample_rate", 0),
            center_frequency_hz=entry.get("center_frequency_hz"), bandwidth_hz=entry.get("bandwidth_hz"),
            models=resolved_models, policy=settings["policy"], context=context, prior_hints=hints[:32])
        result["pipeline"] = self.pipeline_fingerprint
        if hint_count > 32:
            result.setdefault("warnings", []).append(f"Bandhinweise auf 32 von {hint_count} begrenzt; sie beeinflussen keine Klassenaussage.")
        if entry.get("observation_type") == "unresolved_window_probe":
            result.setdefault("warnings", []).append("Prüfung des gesamten Empfangsfensters ohne isolierte Energiedetektion; diese Beobachtung bestätigt noch kein einzelnes Signal.")
            result["input"]["observation_type"] = "unresolved_window_probe"
        effective = [{"id": m["id"], "level": m.get("level", "modulation"), "families": m.get("families", [])} for m in resolved_models]
        result["effective_models"] = effective
        identity = {"settings": settings, "effective_models": effective,
                    "artifact_revision": self._model_revision(resolved_models), "pipeline": self.pipeline_fingerprint,
                    "source_context_revision": _context_revision(entry), "prior_hints": _safe(hints[:32]),
                    "sample_sha256": result.get("input", {}).get("sample_sha256"),
                    "model_artifacts": [{"id": r["model_id"], "hash": r.get("model_hash")} for r in result.get("routes", [])],
                    "input": {k: entry.get(k) for k in ("sample_rate", "center_frequency_hz", "bandwidth_hz", "source_sample_start", "source_sample_stop", "source_sample_stride")}}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        observation = str(entry.get("observation_id") or hashlib.sha256(np.asarray(entry.get("iq", []), np.complex64).tobytes()).hexdigest())
        observation_id = hashlib.sha256(f"{self._signal_id(entry)}|{observation}|{fingerprint}".encode()).hexdigest()
        return self._record(entry, result, observation_id, settings)

    def _record_unavailable(self, entry, status, reason):
        identifier = self._signal_id(entry)
        previous = self.signals.get(identifier)
        if previous:
            if entry.get("ended_at") and previous["status"] in {"not_run_budget", "not_buffered"}:
                self._record(entry, {"schema_version": 1, "status": status, "label": "unbekannt",
                    "conclusions": {}, "stages": [{"id": "acquisition", "parents": [], "status": status,
                    "decision": "Ohne Analyse beendet", "reason": reason}], "warnings": [],
                    "limitations": ["Das beendete Signal hatte kein rechtzeitig analysiertes IQ-Fenster."]})
                return
            if entry.get("ended_at") and previous.get("ended_at") != entry["ended_at"]:
                previous.update(activity="ended", ended_at=entry["ended_at"],
                                end_reason=entry.get("end_reason"), iq_retained=False)
                settings = self.get_settings()
                self.signals.save(previous, max_history=settings["max_history"], max_signals=settings["max_signals"])
            elif status == "not_buffered" and previous["status"] == "not_buffered" and entry.get("observed_at") != previous.get("last_seen"):
                previous.update(last_seen=entry["observed_at"], analysis_at=utcnow())
                settings = self.get_settings()
                self.signals.save(previous, max_history=settings["max_history"], max_signals=settings["max_signals"])
            return
        self._record(entry, {"schema_version": 1, "status": status, "label": "unbekannt",
            "conclusions": {}, "stages": [{"id": "acquisition", "parents": [], "status": status,
            "decision": "Keine Modellentscheidung", "reason": reason}], "warnings": [],
            "limitations": ["Detektion ohne auswertbares IQ-Fenster ist keine Modulationsklassifikation."]})

    def process_snapshots(self):
        # A scan end callback and normal polling must not classify the same IQ twice.
        with self.cycle_lock:
            settings = self.get_settings()
            if not self.classification_enabled():
                with self.lock:
                    self.stats.update(paused_reason="Klassifikation im Empfänger deaktiviert", pending_signals=0,
                                      last_cycle_ms=0., last_error=None)
                return
            with self.lock:
                self.stats["paused_reason"] = None
            if not settings["enabled"] or self.stop_event.is_set():
                return
            started = time.perf_counter()
            snapshot = self.capture.snapshot()
            acquisition = snapshot.get("analysis_status", {})
            for ended in acquisition.get("ended_channels", []):
                if settings["start_frequency_hz"] <= ended.get("center_frequency_hz", -1) <= settings["stop_frequency_hz"]:
                    self._record_unavailable(ended, "insufficient_samples",
                        "Detektierter Kanal endete vor seiner Analyse; IQ wird außerhalb der begrenzten aktiven Ringe nicht aufbewahrt.")
            entries = self.capture.analysis_snapshots()
            if not entries and hasattr(self.capture, "analysis_probe_snapshot"):
                probe = self.capture.analysis_probe_snapshot()
                if probe is not None:
                    entries = [probe]
            entries = [e for e in entries if e.get("center_frequency_hz") is not None and
                       settings["start_frequency_hz"] <= e["center_frequency_hz"] <= settings["stop_frequency_hz"]]
            if entries:
                offset = self.cursor % len(entries)
                entries = entries[offset:] + entries[:offset]
                self.cursor += settings["max_signals_per_cycle"]
            models = self._models(settings)
            model_revision = self._model_revision(models)
            pending = 0
            processed = 0
            for entry in entries:
                identifier = self._signal_id(entry)
                frame = entry.get("observation_id") or hashlib.sha256(np.asarray(entry.get("iq", []), np.complex64).tobytes()).hexdigest()
                observation = (frame, _context_revision(entry), self.revision, model_revision)
                if self.seen.get(identifier) == observation:
                    continue
                if processed >= settings["max_signals_per_cycle"]:
                    pending += 1
                    if self.signals.get(identifier) is None:
                        self._record(entry, {"schema_version": 1, "status": "not_run_budget", "label": "noch nicht analysiert",
                            "conclusions": {}, "stages": [{"id": "budget", "parents": [], "status": "not_run_budget",
                             "decision": "Wartet auf Analyseslot", "reason": "Mehr detektierte Signale als das Zyklusbudget; rotierende Auswahl."}],
                            "warnings": [], "limitations": ["Keine Klassenaussage ohne Analyse."]})
                    continue
                self.analyze_entry(entry, models=models, settings=settings)
                self.seen[identifier] = observation
                processed += 1
            # Never grow the in-memory deduplication map without a bound.
            if len(self.seen) > settings["max_signals"]:
                self.seen = dict(list(self.seen.items())[-settings["max_signals"]:])
            # Detections beyond the DSP channel budget remain inspectable.
            # Their frequency-bin IDs are observation IDs, never emitter IDs.
            receiver = snapshot.get("receiver", {})
            resolution = (snapshot.get("sample_rate") or 1) / max(1, snapshot.get("fft_size", 2048))
            for detection in snapshot.get("detections", []):
                center = detection.get("center_frequency_hz", detection.get("frequency_hz"))
                if center is None or not settings["start_frequency_hz"] <= center <= settings["stop_frequency_hz"]:
                    continue
                if any(abs(center - e["center_frequency_hz"]) <= max(e.get("bandwidth_hz", 0) / 2, resolution) for e in entries):
                    continue
                self._record_unavailable({"channel_id": f"unbuffered:{round(center / resolution)}",
                    "session_id": receiver.get("session_id"), "source_epoch": receiver.get("session_id"),
                    "source": receiver.get("source"), "synthetic": receiver.get("synthetic", False),
                    "observed_at": snapshot.get("timestamp"), "center_frequency_hz": center,
                    "bandwidth_hz": detection.get("bandwidth_hz"), "sample_count": 0,
                    "sample_rate": receiver.get("sample_rate")}, "not_buffered",
                    "Energie detektiert, aber kein separater IQ-Ring im aktuellen Filterbudget. Analysekanalgrenze erhöhen oder Empfangsfenster eingrenzen.")
            with self.lock:
                self.stats.update(cycles=self.stats["cycles"] + 1,
                                  analyzed_observations=self.stats["analyzed_observations"] + processed,
                                  pending_signals=pending, unbuffered_detections=acquisition.get("unbuffered_detections_count", 0),
                                  last_cycle_ms=(time.perf_counter() - started) * 1000, last_error=None)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.process_snapshots()
            except Exception as exc:
                with self.lock:
                    self.stats["last_error"] = str(exc)
                LOG.exception("Signalentscheidung konnte nicht verarbeitet werden")
            self.stop_event.wait(self.get_settings()["interval_seconds"])

    def finish_window(self):
        """Drain a stopped scan tile before its IQ rings are replaced.

        There are at most 16 rings. Costs are a recorded scan analysis pause,
        not additional reception time; native model operations remain cooperative.
        """
        with self.cycle_lock:
            for _ in range(16):
                if not self.get_settings()["enabled"] or self.stop_event.is_set() or not self.classification_enabled():
                    return
                self.process_snapshots()
                if not self.stats["pending_signals"]:
                    return

    def coverage(self):
        receiver = self.capture.status()
        settings = self.get_settings()
        source = receiver.get("source")
        hardware_limits = {"rsp1a": (1000., 2e9), "pluto": (325e6, 3.8e9)}.get(source)
        limits = hardware_limits or (0., 6e9)
        center, rate = receiver.get("center_frequency_hz"), receiver.get("sample_rate")
        current = None
        if center is not None and rate:
            width = .8 * min(rate, 20e6 if source == "pluto" else 10e6 if source == "rsp1a" else rate)
            current = {"start_frequency_hz": max(0., limits[0], center - width / 2),
                       "stop_frequency_hz": min(6e9, limits[1], center + width / 2),
                       "sample_rate": rate, "source": source, "session_id": receiver.get("session_id"),
                       "observed_at": self.capture.snapshot().get("timestamp"), "active": receiver.get("running", False)}
        return {"requested_range": {k: settings[k] for k in ("start_frequency_hz", "stop_frequency_hz")},
                "current_window": current, "receiver_limits": {"source": source,
                    "min_frequency_hz": hardware_limits[0] if hardware_limits else None,
                    "max_frequency_hz": hardware_limits[1] if hardware_limits else None},
                "simultaneous": False, "full_range_covered": False, "coverage_verified": False,
                "scope": "instantaneous_receiver_window",
                "notes": ["Ein Empfänger erfasst nur ein momentanes Frequenzfenster; Scans haben Beobachtungslücken.",
                          "Stock-Pluto: 325 MHz–3,8 GHz; RSP1A: 1 kHz–2 GHz. Der Gesamtbereich 0–6 GHz benötigt weitere Empfangstechnik oder Aufnahmen.",
                          "Energiedetektion kann schwache, kurze, überlagerte oder rauschartige Signale übersehen."]}

    def snapshot(self):
        with self.lock:
            stats = {**self.stats, **self.signals.stats()}
        stats["paused_reason"] = None if self.classification_enabled() else "Klassifikation im Empfänger deaktiviert"
        return {"settings": self.get_settings(), "coverage": self.coverage(), "stats": stats,
                "signals": self.signals.list(100, compact=True)}

    def overlay(self, session_id):
        if not self.classification_enabled():
            return []
        result = []
        for item in self.signals.list(100, session_id):
            if item["status"] == "not_run_budget":
                continue
            result.append({"channel_id": item["channel_id"], "session_id": item["session_id"],
                           "center_frequency_hz": item["center_frequency_hz"], "sample_rate": item["sample_rate"],
                           "bandwidth_hz": item["bandwidth_hz"], "observed_at": item["last_seen"],
                           "status": item["status"], "label": item["decision"]["label"],
                           "decision": item["decision"], "signal_id": item["id"]})
        return result

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)
