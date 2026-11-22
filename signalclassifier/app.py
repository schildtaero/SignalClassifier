"""Local API and experiment orchestration. Hardware and ML remain optional."""
from __future__ import annotations

import hashlib
import asyncio
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__, decision, experiment_log, labels, ml, publication
from .analysis_runtime import AnalysisManager
from .db import Store, utcnow
from .drones import Bridge, DroneStore, capabilities, example_payload
from .receivers import CaptureManager
from .sigmf import read_recording
from .telemetry import ResourceSummary, ResourceTelemetry
from .surveillance import ScanController, build_scan_plan
from .spectrum_stream import SpectrumHub

LOG = logging.getLogger(__name__)
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def clean_id(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or ".." in value:
        raise HTTPException(400, "Ungültige Kennung")
    return value


def finite_json(value):
    if isinstance(value, dict):
        return {str(k): finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    if isinstance(value, np.ndarray):
        return finite_json(value.tolist())
    if isinstance(value, np.generic):
        return finite_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


class EmitterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    service: str = Field(default="Unbekannt", max_length=200)
    standard: str = Field(default="Unbekannt", max_length=200)
    modulation: str = Field(default="Unbekannt", max_length=100)
    frequency_min_hz: float = Field(ge=0, le=100e9, allow_inf_nan=False)
    frequency_max_hz: float = Field(ge=0, le=100e9, allow_inf_nan=False)
    region: str = Field(default="DE-BY", max_length=100)
    confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    source: str = Field(default="Benutzereintrag", max_length=1000)
    notes: str = Field(default="", max_length=4000)


class LabelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    verified: bool = False
    notes: str = Field(default="", max_length=4000)

    @field_validator("label")
    @classmethod
    def meaningful_label(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Klassenlabel darf nicht leer sein")
        return value


class BatchLabelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_ids: list[str] = Field(min_length=1, max_length=1000)
    use_suggestions: bool = True
    label: str | None = Field(default=None, max_length=80)
    verified: bool = True
    overwrite_confirmed: bool = False
    notes: str = Field(default="", max_length=4000)
    expected_labels: dict[str, str] | None = Field(default=None, max_length=1000)


class DeleteRecordingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_ids: list[str] = Field(min_length=1, max_length=10000)
    max_duration_seconds: float | None = Field(default=0.5, gt=0, le=0.5, allow_inf_nan=False)
    expected_files: dict[str, str] = Field(min_length=1, max_length=10000)


class DeleteRecordingsPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recording_ids: list[str] = Field(min_length=1, max_length=10000)


class LiveComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(default_factory=list, max_length=3)


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_priors: bool = False
    region: str = Field(default="DE-BY · Bamberg / Nürnberg", max_length=200)
    station_lat: float | None = Field(default=49.9, ge=-90, le=90, allow_inf_nan=False)
    station_lon: float | None = Field(default=10.9, ge=-180, le=180, allow_inf_nan=False)
    online_map: bool = False


class ExperimentCancelled(Exception):
    pass


class Runtime:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        for name in ("recordings", "models", "experiments", "topologies", "exports"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        self.store = Store(data_dir / "library.sqlite3")
        labels.seed(self.store)
        self.capture = CaptureManager(data_dir)
        self.drones = DroneStore(data_dir / "drones.sqlite3")
        self.bridge = Bridge(self.drones)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="experiment")
        self.publication_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-publication")
        self.closing = False
        self.lock = threading.RLock()
        self.cancel_events: dict[str, threading.Event] = {}
        self.active_experiment = None
        self.benchmark = None
        self.live_model_ids: list[str] = []
        self.live_timings = {}
        self.event_sequences = {}
        self.run_clocks = {}
        self.resource_summaries = {}
        # A previous process cannot still execute these jobs.
        for run in self.store.all("experiment"):
            if (run.get("publication") or {}).get("status") in ("queued", "publishing"):
                run["publication"] = {**run["publication"], "status": "interrupted",
                    "error": "Anwendung während des Uploads beendet; GitHub-Stand prüfen und bei Bedarf erneut veröffentlichen."}
                self.store.put("experiment", run["id"], run)
            if run.get("status") in ("running", "queued", "cancelling"):
                run.update(status="interrupted", error="Anwendung während des Experiments beendet.", finished_at=utcnow())
                self.store.put("experiment", run["id"], run)
                try:
                    self.log_event(run["id"], "interrupted", {"error": run["error"]})
                except OSError:
                    LOG.exception("Unterbrechung konnte nicht im Experimentjournal gespeichert werden")
        if self.store.get("settings", "main") is None:
            self.store.put("settings", "main", SettingsInput().model_dump())
        self.telemetry = ResourceTelemetry(on_sample=self.resource_sample)
        self.analysis = AnalysisManager(self.capture, self.store, data_dir, self.model,
            lambda: (self.store.get("active_model", "main") or {}).get("model_id"),
            lambda frequency: labels.suggest(self.store, frequency))
        self.scanner = ScanController(self.capture, on_window=self.analysis.finish_window)
        self.spectrum_stream = SpectrumHub(self.capture)

    def resource_sample(self, sample):
        with self.lock:
            identifier = self.active_experiment
            state = self.store.get("experiment", identifier) if identifier else None
            if state and state.get("status") in ("queued", "running", "cancelling"):
                self.resource_summaries.setdefault(identifier, ResourceSummary()).add(sample)
                self.log_event(identifier, "resources", sample)

    def recording(self, identifier: str) -> dict:
        clean_id(identifier)
        for recording in self.recordings():
            if str(recording["id"]) == identifier:
                return recording
        raise HTTPException(404, "Aufnahme nicht gefunden")

    def recordings(self) -> list[dict]:
        # Read the small catalogues once, not one SQLite connection per clip.
        with self.lock:
            annotations = {row["id"]: row for row in self.store.all("label")}
            entries = self.store.all("emitter")
            return [self.annotated_recording(recording, annotations, entries) for recording in self.capture.list_recordings()]

    def annotated_recording(self, recording: dict, annotations=None, entries=None, with_suggestions=True) -> dict:
        annotation = (annotations.get(str(recording["id"])) if annotations is not None else self.store.get("label", str(recording["id"])))
        if annotation is None:
            glob = recording.get("metadata", {}).get("global", {})
            if glob.get("signalclassifier:label"):
                annotation = {"recording_id": recording["id"], "label": glob["signalclassifier:label"],
                              "verified": glob.get("signalclassifier:label_verified") is True,
                              "notes": glob.get("signalclassifier:label_notes", ""),
                              "provenance": glob.get("signalclassifier:label_provenance", "sigmf-import"),
                              "suggestion": glob.get("signalclassifier:label_suggestion")}
        result = {**recording, "annotation": annotation}
        if with_suggestions:
            result.update(labels.recording_suggestions(self.store, recording, entries=entries))
        return result

    def write_labels(self, items: list[tuple[dict, dict]]) -> list[dict]:
        """Stage metadata first; roll back file replacements if the DB fails."""
        staged = []
        total_bytes = 0
        try:
            for recording, annotation in items:
                path = self.recording_path(recording)
                total_bytes += path.stat().st_size
                if total_bytes > 32 * 1024**2:
                    raise ValueError("Metadatenumfang für einen Batch zu groß; kleinere Auswahl verwenden")
                doc = json.loads(path.read_text(encoding="utf-8"))
                glob = doc.setdefault("global", {})
                glob.update({"signalclassifier:label": annotation["label"], "signalclassifier:label_verified": annotation["verified"],
                             "signalclassifier:label_notes": annotation.get("notes", ""),
                             "signalclassifier:label_provenance": annotation["provenance"]})
                extensions = glob.setdefault("core:extensions", [])
                if not isinstance(extensions, list):
                    raise ValueError("Ungültige SigMF-Erweiterungsliste")
                if not any(isinstance(extension, dict) and extension.get("name") == "signalclassifier" for extension in extensions):
                    extensions.append({"name": "signalclassifier", "version": "1.0.0", "optional": True})
                if annotation.get("suggestion"):
                    glob["signalclassifier:label_suggestion"] = annotation["suggestion"]
                else:
                    glob.pop("signalclassifier:label_suggestion", None)
                token = uuid.uuid4().hex
                temporary = path.with_name(f".{path.name}.{token}.label-stage")
                backup = path.with_name(f".{path.name}.{token}.label-backup")
                staged.append((path, temporary, backup))
                temporary.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            written = []
            try:
                for path, temporary, backup in staged:
                    path.replace(backup)
                    written.append((path, backup))
                    temporary.replace(path)
                annotations = [value for _, value in items]
                self.store.put_many("label", annotations)
            except Exception:
                for path, backup in reversed(written):
                    backup.replace(path)
                raise
            for _, _, backup in staged:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Label-Metadatensicherung konnte nicht entfernt werden: %s", backup.name)
            return annotations
        finally:
            for _, temporary, _ in staged:
                temporary.unlink(missing_ok=True)

    def batch_labels(self, payload: BatchLabelInput) -> dict:
        label = (payload.label or "").strip()
        if not payload.use_suggestions and not label:
            raise ValueError("Für ein gemeinsames Batchlabel ein Klassenlabel angeben")
        if payload.use_suggestions and label:
            raise ValueError("Entweder individuelle Vorschläge oder ein gemeinsames Label wählen")
        with self.lock:
            catalogue = {recording["id"]: recording for recording in self.recordings()}
            identifiers = list(dict.fromkeys(payload.recording_ids))
            for identifier in identifiers:
                clean_id(identifier)
                if identifier not in catalogue:
                    raise HTTPException(404, f"Aufnahme nicht gefunden: {identifier}")
            updates, skipped = [], []
            now = utcnow()
            for identifier in identifiers:
                recording = catalogue[identifier]
                current = recording.get("annotation") or {}
                suggestion = recording.get("label_suggestion")
                if current.get("verified") and not payload.overwrite_confirmed:
                    skipped.append({"id": identifier, "reason": "Label bereits bestätigt"})
                    continue
                if recording.get("error"):
                    skipped.append({"id": identifier, "reason": "Aufnahmemetadaten sind ungültig"})
                    continue
                if payload.use_suggestions and (not suggestion or suggestion.get("ambiguous")):
                    skipped.append({"id": identifier, "reason": recording.get("label_suggestion_reason") or "Kein eindeutiger Frequenzvorschlag; Label manuell wählen"})
                    continue
                selected = suggestion["label"] if payload.use_suggestions else label
                if payload.use_suggestions and payload.expected_labels is not None and payload.expected_labels.get(identifier) != selected:
                    skipped.append({"id": identifier, "reason": "Frequenzvorschlag wurde seit der Vorschau geändert; bitte erneut prüfen"})
                    continue
                if not isinstance(selected, str) or not selected.strip() or len(selected) > 80:
                    raise ValueError("Klassenlabel muss 1–80 Zeichen enthalten")
                annotation = {"id": identifier, "recording_id": identifier, "label": selected.strip(), "verified": payload.verified,
                              "notes": payload.notes, "updated_at": now,
                              "provenance": "human-confirmed-frequency-prior" if payload.use_suggestions and payload.verified else "human-confirmed" if payload.verified else "human-provisional"}
                if payload.use_suggestions:
                    annotation["suggestion"] = suggestion
                updates.append((recording, annotation))
            updated = self.write_labels(updates) if updates else []
            return {"updated": updated, "skipped": skipped, "count": len(updated), "selected_count": len(identifiers)}

    def recording_path(self, recording: dict) -> Path:
        raw = recording.get("metadata_path") or recording.get("path")
        path = Path(raw) if raw else self.data_dir / "recordings" / (recording["id"] + ".sigmf-meta")
        if not path.is_absolute():
            path = self.data_dir / "recordings" / path
        if not path.resolve().is_relative_to((self.data_dir / "recordings").resolve()):
            raise HTTPException(400, "Aufnahme liegt außerhalb der Bibliothek")
        return path

    @staticmethod
    def recording_fingerprint(paths: tuple[Path, Path]) -> str:
        """Cheap identity/change check; never hash multi-megabyte IQ payloads."""
        states = []
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Nur reguläre SigMF-Dateipaare dürfen gelöscht werden")
            stat = path.stat()
            states.append((path.name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return hashlib.sha256(json.dumps(states).encode()).hexdigest()

    def cleanup_preview(self, max_duration_seconds: float | None = 0.5,
                        recording_ids: list[str] | None = None) -> dict:
        if max_duration_seconds is not None and (not math.isfinite(max_duration_seconds) or not 0 < max_duration_seconds <= 0.5):
            raise ValueError("Bereinigungsgrenze muss größer als 0 und höchstens 0,5 Sekunden sein")
        if max_duration_seconds is None and recording_ids is None:
            raise ValueError("Ohne Dauergrenze müssen Aufnahmen ausdrücklich ausgewählt werden")
        if recording_ids is not None:
            if not isinstance(recording_ids, list) or not 1 <= len(recording_ids) <= 10000:
                raise ValueError("Zwischen 1 und 10000 Aufnahmen auswählen")
            recording_ids = list(dict.fromkeys(clean_id(identifier) for identifier in recording_ids))
        with self.lock:
            status = self.capture.status()
            blocked = ("Zuerst Empfang beziehungsweise Replay stoppen; währenddessen werden keine Aufnahmen gelöscht."
                       if status.get("busy") else None)
            if self.scanner.status().get("running"):
                blocked = "Zuerst den Scan stoppen; während eines Scans werden keine Aufnahmen gelöscht."
            protected, references = set(), {}
            for experiment in self.store.all("experiment"):
                config = experiment.get("config") or {}
                identifiers = set(config.get("recording_ids") or [])
                identifiers.update(entry.get("id") for entry in config.get("recordings", []) if isinstance(entry, dict) and entry.get("id"))
                for identifier in identifiers:
                    references[identifier] = references.get(identifier, 0) + 1
                if experiment.get("status") in ("queued", "running", "cancelling"):
                    protected.update(identifiers)
            annotations = {row["id"]: row for row in self.store.all("label")}
            selected, skipped = [], []
            catalogue = self.capture.list_recordings()
            if recording_ids is not None:
                by_id = {recording["id"]: recording for recording in catalogue}
                skipped.extend({"id": identifier, "reason": "Aufnahme nicht gefunden; Vorschau erneut öffnen"}
                               for identifier in recording_ids if identifier not in by_id)
                catalogue = [by_id[identifier] for identifier in recording_ids if identifier in by_id]
            for recording in catalogue:
                identifier = recording["id"]
                duration = recording.get("duration_seconds")
                if recording.get("error") or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
                    skipped.append({"id": identifier, "reason": "Dauer unbekannt oder SigMF-Dateipaar ungültig"})
                    continue
                if max_duration_seconds is not None and duration >= max_duration_seconds:
                    continue
                if identifier in protected:
                    skipped.append({"id": identifier, "reason": "Aufnahme wird von einem laufenden oder vorgemerkten Experiment verwendet"})
                    continue
                try:
                    path = self.recording_path(recording)
                    paths = (path, path.with_suffix(".sigmf-data"))
                    fingerprint = self.recording_fingerprint(paths)
                    size = sum(file.stat().st_size for file in paths)
                except (ValueError, OSError, HTTPException) as exc:
                    skipped.append({"id": identifier, "reason": str(getattr(exc, "detail", exc))})
                    continue
                annotation = self.annotated_recording(recording, annotations, with_suggestions=False).get("annotation") or {}
                selected.append({"id": identifier, "filename": path.name, "name": recording.get("name", identifier),
                                 "duration_seconds": duration, "bytes": size,
                                 "label": annotation.get("label"), "verified": annotation.get("verified") is True,
                                 "experiment_count": references.get(identifier, 0), "fingerprint": fingerprint})
            if len(selected) > 10000:
                blocked = "Mehr als 10000 kurze Aufnahmen gefunden; Bibliothek für eine Bereinigung in kleinere Teile aufteilen."
            referenced = sum(row["experiment_count"] > 0 for row in selected)
            return {"max_duration_seconds": max_duration_seconds, "recordings": selected, "count": len(selected),
                    "total_bytes": sum(row["bytes"] for row in selected), "skipped": skipped, "blocked_reason": blocked,
                    "expected_files": {row["id"]: row["fingerprint"] for row in selected},
                    "warnings": ([f"{referenced} ausgewählte Aufnahmen werden von bereits beendeten Experimenten referenziert. "
                                  "Gespeicherte Berichte bleiben erhalten; die IQ-Daten stehen danach für erneute Auswertungen nicht mehr zur Verfügung."]
                                 if referenced else [])}

    def delete_recordings(self, payload: DeleteRecordingsInput) -> dict:
        """Remove explicit reviewed, unchanged pairs; stage before DB commit."""
        with self.lock:
            identifiers = list(dict.fromkeys(payload.recording_ids))
            preview = self.cleanup_preview(payload.max_duration_seconds,
                                           identifiers if payload.max_duration_seconds is None else None)
            if preview["blocked_reason"]:
                raise HTTPException(409, preview["blocked_reason"])
            catalogue = {recording["id"]: recording for recording in self.capture.list_recordings()}
            for identifier in identifiers:
                clean_id(identifier)
                if identifier not in catalogue:
                    raise HTTPException(404, f"Aufnahme nicht gefunden: {identifier}; Vorschau erneut öffnen")
            candidates = {row["id"]: row for row in preview["recordings"]}
            reasons = {row["id"]: row["reason"] for row in preview["skipped"]}
            selected, skipped = [], []
            for identifier in identifiers:
                row = candidates.get(identifier)
                if row is None:
                    skipped.append({"id": identifier, "reason": reasons.get(identifier,
                        "Aufnahme ist nicht kürzer als die gewählte Grenze" if payload.max_duration_seconds is not None
                        else "Aufnahme ist nicht für die ausgewählte Löschung freigegeben")})
                elif payload.expected_files.get(identifier) != row["fingerprint"]:
                    skipped.append({"id": identifier, "reason": "Dateien wurden seit der Vorschau geändert; bitte erneut prüfen"})
                else:
                    selected.append(row)
            result = {"deleted": [], "count": 0, "bytes_deleted": 0, "skipped": skipped, "cleanup_warning": None}
            if not selected:
                return result
            staging = Path(tempfile.mkdtemp(prefix=".delete-", dir=self.data_dir / "recordings"))
            moved = []
            try:
                try:
                    for row in selected:
                        path = self.recording_path(catalogue[row["id"]])
                        paths = (path, path.with_suffix(".sigmf-data"))
                        if self.recording_fingerprint(paths) != row["fingerprint"]:
                            raise ValueError("Aufnahmedateien wurden während der Bereinigung geändert; Vorschau erneut öffnen")
                        # Metadata is the publication marker: hide it first and restore it last.
                        for original in paths:
                            staged = staging / original.name
                            original.replace(staged)
                            moved.append((original, staged))
                    self.store.delete_many("label", [row["id"] for row in selected])
                except Exception:
                    for original, staged in reversed(moved):
                        staged.replace(original)
                    raise
                result.update(deleted=[row["id"] for row in selected], count=len(selected))
                for _, staged in moved:
                    try:
                        size = staged.stat().st_size
                        staged.unlink()
                        result["bytes_deleted"] += size
                    except OSError:
                        LOG.exception("Aufnahme aus Bibliothek entfernt, temporäre Datei bleibt erhalten: %s", staged)
                        result["cleanup_warning"] = f"Aus der Bibliothek entfernt; temporäre Dateien in {staging.name} konnten nicht vollständig gelöscht werden."
                return result
            finally:
                try:
                    staging.rmdir()
                except OSError:
                    LOG.warning("Bereinigungsordner blieb erhalten: %s", staging)

    def model(self, identifier: str) -> dict:
        value = self.store.get("model", clean_id(identifier))
        if value is None:
            raise HTTPException(404, "Modell nicht gefunden")
        return value

    def active_model_path(self):
        selected = self.store.get("active_model", "main")
        if not selected:
            return None
        return self.model(selected["model_id"])["model_path"]

    def live_comparison_settings(self):
        with self.lock:
            models = [self.model(identifier) for identifier in self.live_model_ids]
            return {"model_ids": list(self.live_model_ids), "active": bool(models), "sampling": "latest_iq",
                    "models": [{"id": model["id"], "name": model.get("name", model["id"]),
                                "topology_id": model.get("topology_id"), "evaluation_available": model.get("evaluation_available", False)} for model in models],
                    "message": "Live-Stichproben derselben aktuellen IQ-Daten; ohne Referenzlabel keine Live-Genauigkeit."}

    def compare_latest(self, iq, sample_rate, center_frequency, snapshot, primary=None):
        if not self.analysis.classification_enabled():
            return []
        with self.lock:
            selected = list(self.live_model_ids)
        active = self.store.get("active_model", "main") or {}
        use_priors = self.store.get("settings", "main").get("use_priors", False)
        observation = (snapshot.get("receiver", {}).get("session_id"), snapshot.get("timestamp"), snapshot.get("sample_index"))
        source_key = (observation[0], sample_rate, center_frequency)
        results = []
        for identifier in selected:
            model = self.model(identifier)
            result = {"model_id": identifier, "name": model.get("name", identifier), "classification": None,
                      "elapsed_ms": None, "real_time_factor": None, "model_segment_length": None, "sample_rate": sample_rate,
                      "rolling": {"latency_ms_p50": None, "latency_ms_p95": None, "real_time_factor_p95": None, "observations": 0, "warmup": True}, "error": None}
            try:
                metadata = ml.load_model_metadata(model["model_path"])
                length = metadata["segment_length"]
                result["model_segment_length"] = length
                if iq is None or not len(iq) or sample_rate <= 0:
                    result["error"] = "Noch keine IQ-Daten; Empfang starten."
                    results.append(result)
                    continue
                reuse_primary = active.get("model_id") == identifier and primary and not primary.get("explanation")
                prediction = primary if reuse_primary else ml.classify(
                    iq[-length:], sample_rate, center_frequency_hz=center_frequency, use_priors=use_priors,
                    model_path=model["model_path"], attribution=False)
                duration = min(len(iq), length) / sample_rate
                full_inference = prediction.get("confidence_kind") == "uncalibrated_model_score" and not prediction.get("skipped_stages")
                elapsed = prediction["elapsed_ms"]
                ratio = elapsed / 1000 / duration if full_inference and duration > 0 else None
                result.update(classification=prediction, elapsed_ms=elapsed, real_time_factor=ratio)
                with self.lock:
                    timing = self.live_timings.get(identifier)
                    if timing is None or timing["source"] != source_key:
                        timing = {"source": source_key, "calls": 0, "last_observation": None, "values": deque(maxlen=30)}
                        self.live_timings[identifier] = timing
                    if full_inference and timing["last_observation"] != observation:
                        timing["calls"] += 1
                        timing["last_observation"] = observation
                        if timing["calls"] > 3:
                            timing["values"].append((elapsed, ratio))
                    values = list(timing["values"])
                    result["rolling"].update(observations=len(values), warmup=timing["calls"] <= 3)
                    if values:
                        latencies, ratios = np.asarray(values).T
                        result["rolling"].update(latency_ms_p50=float(np.median(latencies)), latency_ms_p95=float(np.percentile(latencies, 95)),
                                                 real_time_factor_p95=float(np.percentile(ratios, 95)))
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                result["error"] = str(exc)
            results.append(result)
        return results

    def topologies(self):
        catalogue = {item["id"]: item for item in ml.list_topologies()}
        for item in self.store.all("topology"):
            catalogue[item["id"]] = item
        return list(catalogue.values())

    def log_event(self, identifier: str, event: str, data: dict):
        with self.lock:
            path = self.data_dir / "experiments" / clean_id(identifier) / "events.jsonl"
            if identifier not in self.event_sequences:
                self.event_sequences[identifier] = max((row.get("sequence", 0) for row in experiment_log.read_events(path)), default=0)
            sequence = self.event_sequences[identifier] + 1
            elapsed = time.perf_counter() - self.run_clocks[identifier] if identifier in self.run_clocks else None
            experiment_log.append_event(path, sequence, event, finite_json(data), elapsed)
            self.event_sequences[identifier] = sequence

    def freeze_experiment_inputs(self, identifier: str, config: dict) -> dict:
        folder = self.data_dir / "experiments" / identifier
        annotations = {row["id"]: row for row in self.store.all("label")}
        inventory = experiment_log.dataset_inventory(config.get("recordings", []), annotations)
        experiment_log.write_json(folder / "configuration.json", finite_json(config))
        experiment_log.write_json(folder / "dataset-inventory.json", finite_json(inventory))
        provenance = finite_json({"schema_version": 1, "snapshot_utc": utcnow(),
            "software": experiment_log.software_provenance(), "hardware_at_queue": ml.hardware_profile(),
            "settings_at_queue": self.store.get("settings", "main"), "source": config["source"],
            "configuration_sha256": experiment_log.canonical_sha256(finite_json(config)),
            "inventory_sha256": experiment_log.canonical_sha256(finite_json(inventory)),
            "snapshot_hash_format": "SHA-256 of UTF-8 JSON, sorted keys, ensure_ascii=false, separators=(',', ':'), no NaN; independent of ZIP JSON whitespace.",
            "recording_count": len(inventory),
            "same_session_consent": config.get("allow_same_session", False),
            "partition_unit": "original_recording" if config.get("allow_same_session") else "origin_session",
            "references": [{"title": "SignalClassifier: Topologien und Bewertungsmethodik", "url": "https://github.com/schildtaero/SignalClassifier/blob/main/docs/topologien.md"},
                           {"title": "Condition Monitoring for Electric Aircraft Propulsion Systems", "url": "https://doi.org/10.25439/rmt.33772282",
                            "scope": "FB-LSTM/2D-CNN/5-CNN are documented RF adaptations, not exact reproductions of audio networks."}],
            "notes": "Immutable queue-time metadata and labels. No full IQ payloads or model weights are included in report bundles."})
        experiment_log.write_json(folder / "provenance.json", provenance)
        return provenance

    def partial_reports(self, identifier: str) -> list[dict]:
        folder = self.data_dir / "experiments" / clean_id(identifier)
        reports = []
        paths = sorted(set(folder.glob("topology-*/report.json")) | set(folder.rglob("optimization.json")))
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                reports.append({"path": path.relative_to(folder).as_posix(), "kind": path.stem, "report": value})
            except (OSError, ValueError) as exc:
                reports.append({"path": path.relative_to(folder).as_posix(), "error": str(exc)})
        return reports

    def paper_package(self, identifier: str) -> dict[str, bytes]:
        """Project only frozen scientific structure/results, never the private ZIP."""
        with self.lock:
            value = self.store.get("experiment", clean_id(identifier))
            if value is None:
                raise HTTPException(404, "Experiment nicht gefunden")
            if value.get("status") in ("queued", "running", "cancelling"):
                raise HTTPException(409, "Paper-Daten sind nach dem Ende des Experiments verfügbar")
            folder = self.data_dir / "experiments" / identifier
            inventory_path = folder / "dataset-inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.is_file() else []
            value["partial_reports"] = self.partial_reports(identifier)
            events = experiment_log.read_events(folder / "events.jsonl")
        return publication.make_publication_package(value, dataset_inventory=inventory, events=events)

    def queue_publication(self, identifier: str) -> dict:
        with self.lock:
            value = self.store.get("experiment", clean_id(identifier))
            if value is None:
                raise HTTPException(404, "Experiment nicht gefunden")
            if value.get("status") in ("queued", "running", "cancelling"):
                raise HTTPException(409, "Veröffentlichung ist erst nach dem Experiment möglich")
            settings = publication.normalize_publication(value.get("config", {}).get("github_publication"))
            if not settings["enabled"]:
                raise HTTPException(400, "GitHub-Veröffentlichung wurde für dieses Experiment nicht aktiviert")
            previous = value.get("publication") or {}
            if previous.get("status") in ("publishing", "published") or (
                    previous.get("status") == "queued" and previous.get("started_at")):
                return previous
            if self.closing:
                raise HTTPException(409, "Anwendung wird beendet; Veröffentlichung später erneut starten")
            state = {"status": "queued", "repository": settings["repository"], "branch": settings["branch"],
                     "started_at": utcnow(), "error": None}
            value["publication"] = state
            self.store.put("experiment", identifier, value)
            try:
                self.publication_executor.submit(self.publish_experiment, identifier, settings)
            except Exception as exc:
                state.update(status="failed", error=publication.safe_error(exc), finished_at=utcnow())
                value["publication"] = state
                self.store.put("experiment", identifier, value)
            return state

    def publish_experiment(self, identifier: str, settings: dict):
        try:
            with self.lock:
                value = self.store.get("experiment", identifier)
                value["publication"].update(status="publishing")
                self.store.put("experiment", identifier, value)
            files = self.paper_package(identifier)
            result = publication.publish_package(settings, identifier, files)
            result = {**result, "status": "published", "error": None, "finished_at": utcnow()}
        except Exception as exc:
            # Raw exceptions, request headers and remote bodies can contain secrets.
            result = {"status": "failed", "error": publication.safe_error(exc), "finished_at": utcnow()}
            LOG.warning("Paper-Veröffentlichung fehlgeschlagen: %s (%s)", identifier, type(exc).__name__)
        with self.lock:
            value = self.store.get("experiment", identifier)
            value["publication"].update(result)
            self.store.put("experiment", identifier, value)

    def run(self, identifier: str, config: dict):
        event = self.cancel_events[identifier]
        def progress(value):
            with self.lock:
                if event.is_set():
                    raise ExperimentCancelled()
                self.log_event(identifier, "progress", value if isinstance(value, dict) else {"message": str(value)})
                state = self.store.get("experiment", identifier)
                if isinstance(value, dict):
                    immutable = {"id", "config", "name", "created_at", "provenance", "log_files", "report", "model_id", "model_ids"}
                    state.update({key: data for key, data in value.items() if key not in immutable})
                state["status"] = "running"
                self.store.put("experiment", identifier, finite_json(state))
        try:
            with self.lock:
                self.active_experiment = identifier
                self.run_clocks[identifier] = time.perf_counter()
            progress({"progress": 0, "stage": "initializing", "started_at": utcnow()})
            report = ml.run_comparison(config, self.data_dir / "experiments" / identifier, progress=progress)
            if event.is_set():
                raise ExperimentCancelled()
            report = finite_json(report)
            comparison = report["comparison"]
            model_records, copied = [], []
            selected_model_id = None
            try:
                for result in comparison:
                    if event.is_set():
                        raise ExperimentCancelled()
                    model_path = Path(result["model_path"]).resolve()
                    if not model_path.is_dir() or not model_path.is_relative_to(self.data_dir.resolve()):
                        raise ValueError("Experiment hat keinen gültigen Modellordner erzeugt")
                    model_id = uuid.uuid4().hex
                    destination = self.data_dir / "models" / model_id
                    copied.append(destination)
                    shutil.copytree(model_path, destination)
                    model_metadata = ml.load_model_metadata(destination)
                    artifact_sha256 = {path.name: experiment_log.file_sha256(path) for path in sorted(destination.iterdir()) if path.is_file() and not path.is_symlink()}
                    name = config.get("name") or result["name"]
                    if len(comparison) > 1:
                        name = f"{name} · {result['name']}" if config.get("name") else result["name"]
                    model_records.append(finite_json({**model_metadata, "id": model_id, "name": name,
                        "model_path": str(destination), "experiment_id": identifier, "created_at": utcnow(), "metrics": result.get("metrics", {}),
                        "score": result.get("score"), "score_rank": result.get("score_rank"), "accuracy_rank": result.get("accuracy_rank"),
                        "assessment_hardware": result.get("report", {}).get("hardware"), "artifact_sha256": artifact_sha256}))
                    result["model_id"] = model_id
                    result["artifact_sha256"] = artifact_sha256
                    if result["model_path"] == report["model_path"]:
                        selected_model_id = model_id
                self.store.put_many("model", model_records)
            except Exception:
                for path in copied:
                    shutil.rmtree(path, ignore_errors=True)
                raise
            model_ids = [value["id"] for value in model_records]
            with self.lock:
                report["resource_telemetry"] = self.resource_summaries.get(identifier, ResourceSummary()).snapshot()
            state = self.store.get("experiment", identifier)
            state.update(status="completed", progress=1, report=report, metrics=report.get("metrics", {}), model_id=selected_model_id, model_ids=model_ids, finished_at=utcnow())
            self.store.put("experiment", identifier, state)
            self.log_event(identifier, "completed", {"model_ids": model_ids, "selected_model_id": selected_model_id,
                "metrics": report.get("metrics"), "rankings": report.get("rankings"),
                "model_artifacts": [{"model_id": row["id"], "artifact_sha256": row["artifact_sha256"]} for row in model_records]})
        except ExperimentCancelled:
            state = self.store.get("experiment", identifier)
            state.update(status="cancelled", finished_at=utcnow())
            self.store.put("experiment", identifier, state)
            self.log_event(identifier, "cancelled", {"finished_at": state["finished_at"]})
        except Exception as exc:
            LOG.exception("Experiment %s failed", identifier)
            state = self.store.get("experiment", identifier)
            state.update(status="failed", error=str(exc), finished_at=utcnow())
            self.store.put("experiment", identifier, state)
            try:
                self.log_event(identifier, "failed", {"error": str(exc), "error_type": type(exc).__name__})
            except OSError:
                LOG.exception("Experimentfehler konnte nicht im Journal gespeichert werden")
        finally:
            with self.lock:
                self.active_experiment = None
            if config.get("github_publication", {}).get("enabled") and not self.closing:
                try:
                    self.queue_publication(identifier)
                except Exception as exc:
                    with self.lock:
                        state = self.store.get("experiment", identifier)
                        state["publication"] = {"status": "failed", "error": publication.safe_error(exc), "finished_at": utcnow()}
                        self.store.put("experiment", identifier, state)

    def close(self):
        self.closing = True
        self.spectrum_stream.close()
        for event in self.cancel_events.values():
            event.set()
        self.scanner.stop()
        self.analysis.close()
        self.capture.stop()
        self.bridge.stop()
        self.telemetry.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.publication_executor.shutdown(wait=False, cancel_futures=True)


async def copy_upload(upload: UploadFile, destination: Path, limit: int) -> int:
    size = 0
    with destination.open("wb") as output:
        while block := await upload.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise HTTPException(413, f"Datei überschreitet die Grenze von {limit // (1024 * 1024)} MiB")
            output.write(block)
    return size


def validate_experiment(config: dict, rt: Runtime) -> dict:
    if any(str(key).startswith("_") for key in config):
        raise ValueError("Interne Experimentparameter dürfen nicht über die API gesetzt werden")
    value = {"source": "synthetic", "topology_id": "feature-lda", "seed": 42, "samples_per_class": 80,
             "segment_length": 2048, "snr_db": [0, 10, 20], "jitter": [0.0], "epochs": 5,
             "learning_rate": 0.001, "batch_size": 32, "device": "auto", "use_priors": False, "evaluation_mode": "independent", **config}
    value.setdefault("allow_same_session", False)
    if type(value["allow_same_session"]) is not bool:
        raise ValueError("allow_same_session muss ein boolescher Wert sein")
    value["github_publication"] = publication.normalize_publication(value.get("github_publication"))
    if not isinstance(value.get("experiment_notes", ""), str) or len(value.get("experiment_notes", "")) > 12000:
        raise ValueError("Versuchsbeschreibung muss Text mit höchstens 12000 Zeichen sein")
    if value["source"] not in ("synthetic", "recordings"):
        raise ValueError("Quelle muss synthetic oder recordings sein")
    catalogue = {t["id"]: t for t in rt.topologies()}
    identifiers = value.get("topology_ids", [value["topology_id"]])
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 50 or not all(isinstance(item, str) for item in identifiers):
        raise ValueError("1–50 Topologien für das Experiment auswählen")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Eine Topologie darf pro Vergleich nur einmal ausgewählt sein")
    if any(identifier not in catalogue for identifier in identifiers):
        raise ValueError("Topologie unbekannt")
    value.update(topology_id=identifiers[0], topology_ids=identifiers,
                 topology=catalogue[identifiers[0]], topologies=[catalogue[identifier] for identifier in identifiers])
    for key, low, high in (("seed", 0, 2**31 - 1), ("samples_per_class", 12, 1000), ("segment_length", 256, 65536), ("epochs", 1, 100), ("batch_size", 4, 256)):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError(f"{key}: Ganzzahl zwischen {low} und {high} erforderlich")
    if value["device"] not in ("auto", "cpu", "cuda"):
        raise ValueError("device muss auto, cpu oder cuda sein")
    if value["evaluation_mode"] not in ("independent", "training_only"):
        raise ValueError("Bewertung muss independent oder training_only sein")
    if not isinstance(value["use_priors"], bool):
        raise ValueError("use_priors muss boolesch sein")
    if not isinstance(value["learning_rate"], (int, float)) or not 1e-5 <= value["learning_rate"] <= 0.1:
        raise ValueError("Ungültige Lernrate")
    if len(str(value.get("name", ""))) > 200:
        raise ValueError("Experimentname zu lang")
    for key, low, high in (("snr_db", -30, 80), ("jitter", 0, 2)):
        entries = value[key]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 20 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not low <= x <= high for x in entries):
            raise ValueError(f"{key}: 1–20 Zahlen im Bereich {low} bis {high} erforderlich")
    # Prevent accidentally reserving many gigabytes on a laptop.
    if value["samples_per_class"] * value["segment_length"] > 64_000_000:
        raise ValueError("Datensatz zu groß: Segmentlänge oder Anzahl reduzieren (64 Mio. Samples je Klasse)")
    if len(value["snr_db"]) * len(value["jitter"]) > 60:
        raise ValueError("Maximal 60 SNR-/Jitter-Kombinationen pro Experiment")
    value.pop("recordings", None)  # Never accept arbitrary filesystem paths from a client.
    if value["source"] == "recordings":
        ids = value.get("recording_ids", [])
        if not isinstance(ids, list) or not 2 <= len(ids) <= 1000:
            raise ValueError("Mindestens zwei bestätigte Aufnahmen unterschiedlicher Klassen auswählen")
        if not all(isinstance(identifier, str) for identifier in ids):
            raise ValueError("Aufnahmekennungen müssen Zeichenketten sein")
        recordings = []
        catalogue = {recording["id"]: recording for recording in rt.recordings()}
        roots = {}

        def recording_root(identifier, ancestors=()):
            if identifier in roots:
                return roots[identifier]
            if identifier in ancestors or len(ancestors) >= 64:
                raise ValueError("Zyklische oder zu tiefe Herkunftskette einer Aufnahme")
            item = catalogue.get(identifier, {})
            glob = item.get("metadata", {}).get("global", {})
            origin = glob.get("sc:origin_recording_id") or glob.get("signalclassifier:origin_recording_id")
            parent = glob.get("sc:parent_recording_id") or glob.get("signalclassifier:parent_recording_id")
            if origin:
                reference = str(origin).removeprefix("recording:")
                root = (recording_root(reference, (*ancestors, identifier))
                        if str(origin).startswith("recording:") and reference != identifier and reference in catalogue else str(origin))
            elif parent:
                root = recording_root(str(parent), (*ancestors, identifier))
            else:
                root = "recording:" + str(glob.get("sc:recording_id") or identifier)
            roots[identifier] = root
            return root

        for identifier in dict.fromkeys(ids):
            clean_id(identifier)
            if identifier not in catalogue:
                raise HTTPException(404, f"Aufnahme nicht gefunden: {identifier}")
            recording = catalogue[identifier]
            annotation = recording.get("annotation") or {}
            label = str(annotation.get("label", "")).strip()
            if not annotation.get("verified") or not label:
                raise ValueError("Alle Trainingsaufnahmen benötigen ein bestätigtes Klassenlabel")
            path = rt.recording_path(recording)
            meta = json.loads(path.read_text(encoding="utf-8"))
            global_meta = meta.get("global", {})
            group = global_meta.get("signalclassifier:group_id") or global_meta.get("sc:origin_group_id") or global_meta.get("signalclassifier:session_id") or global_meta.get("sc:session_id") or global_meta.get("signalclassifier:parent_recording_id") or recording.get("group_id") or identifier
            recordings.append({"path": str(path), "metadata_path": str(path), "label": label, "group_id": str(group), "id": identifier, "verified": True})
            if value["allow_same_session"]:
                recordings[-1]["recording_group_id"] = recording_root(identifier)
        value["recordings"] = recordings
    return ml.validate_experiment_config(value)


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    directory = Path(data_dir or os.environ.get("SIGNALCLASSIFIER_DATA", Path.home() / ".signalclassifier")).expanduser().resolve()
    runtime = Runtime(directory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        runtime.close()

    app = FastAPI(title="SignalClassifier", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime

    @app.middleware("http")
    async def local_api_security(request: Request, call_next):
        # Browser requests from unrelated origins must not operate local USB devices.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Fremder Browser-Ursprung nicht erlaubt"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def runtime_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(OSError)
    async def file_error(request, exc):
        LOG.error("Dateizugriff fehlgeschlagen: %s", exc)
        return JSONResponse({"detail": f"Dateizugriff fehlgeschlagen: {exc}"}, status_code=503)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        LOG.error("Bibliotheksdatenbank nicht verfügbar: %s", exc)
        return JSONResponse({"detail": f"Bibliotheksdatenbank nicht verfügbar: {exc}"}, status_code=503)

    @app.get("/api/status")
    def status():
        recordings = runtime.capture.list_recordings()
        annotations = {row["id"]: row for row in runtime.store.all("label")}
        return finite_json({"version": __version__, "receiver": runtime.capture.status(), "hardware": hardware_info(),
            "telemetry": runtime.telemetry.snapshot(),
            "counts": {"recordings": len(recordings), "labels": sum(bool(runtime.annotated_recording(r, annotations, with_suggestions=False).get("annotation")) for r in recordings),
                       "experiments": runtime.store.count("experiment"), "models": runtime.store.count("model")},
            "active_experiment": runtime.store.get("experiment", runtime.active_experiment) if runtime.active_experiment else None,
            "active_model": runtime.store.get("active_model", "main"),
            "spectrum_stream": runtime.spectrum_stream.stats()})

    @app.get("/api/receiver/status")
    def receiver_status():
        return finite_json(runtime.capture.status())

    @app.post("/api/receiver/start")
    def receiver_start(config: dict):
        with runtime.lock:
            if runtime.scanner.status().get("running"):
                raise ValueError("Ein Scan läuft; vor manueller Abstimmung zuerst den Scan stoppen")
            if not runtime.capture.status().get("busy") and config.get("classification_enabled", True) is not False:
                runtime.analysis.finish_window()
            config = dict(config)
            config.pop("recording_path", None)
            if config.get("source") == "recording":
                config["recording_path"] = str(runtime.recording_path(runtime.recording(config.get("recording_id", ""))))
            with runtime.analysis.cycle_lock:
                return finite_json(runtime.capture.start(config))

    @app.post("/api/receiver/stop")
    def receiver_stop():
        with runtime.lock:
            if runtime.scanner.status().get("running"):
                raise ValueError("Ein Scan läuft; zum Beenden den Scan stoppen")
            return finite_json(runtime.capture.stop())

    @app.get("/api/spectrum")
    def spectrum():
        snapshot = runtime.capture.snapshot()
        enabled = snapshot.get("receiver", {}).get("config", {}).get("classification_enabled", True) is not False
        snapshot["classification_enabled"] = enabled
        if not enabled:
            snapshot.update(classification={"enabled": False, "status": "disabled", "label": "unbekannt",
                "modulation": "unbekannt", "confidence": None, "confidence_kind": "classification_disabled",
                "reason": "Klassifikation im Empfänger deaktiviert; nur Energiedetektion und Aufnahme.",
                "stages": [], "predictions": []}, classification_comparison=[], signal_classifications=[])
            snapshot["analysis_stats"] = finite_json({**runtime.analysis.stats, **runtime.analysis.signals.stats(),
                "paused_reason": "Klassifikation im Empfänger deaktiviert"})
            return finite_json(snapshot)
        iq, sample_rate, center_frequency = runtime.capture.inference_segment()
        if iq is not None and len(iq):
            settings = runtime.store.get("settings", "main")
            model_path = runtime.active_model_path()
            length = ml.load_model_metadata(model_path)["segment_length"] if model_path else 16384
            snapshot["classification"] = finite_json(ml.classify(iq[-length:], sample_rate,
                center_frequency_hz=center_frequency, use_priors=settings.get("use_priors", False), model_path=model_path,
                attribution=False))
            snapshot["classification"].update(center_frequency_hz=center_frequency, sample_rate=sample_rate)
            channel = snapshot.get("classification_channel") or {}
            if channel.get("center_frequency_hz") == center_frequency and channel.get("sample_rate") == sample_rate:
                snapshot["classification"]["bandwidth_hz"] = channel.get("bandwidth_hz")
        snapshot["classification_comparison"] = finite_json(runtime.compare_latest(iq, sample_rate, center_frequency, snapshot, snapshot.get("classification")))
        snapshot["signal_classifications"] = finite_json(runtime.analysis.overlay(snapshot.get("receiver", {}).get("session_id")))
        snapshot["analysis_stats"] = finite_json({**runtime.analysis.stats, **runtime.analysis.signals.stats()})
        return finite_json(snapshot)

    @app.get("/api/spectrum/frame")
    def spectrum_frame(bins: int = 1024):
        if bins != 1024:
            raise ValueError("Die Liveanzeige unterstützt 1024 Spektralbins")
        frame = runtime.capture.display_snapshot(max_bins=bins)
        return finite_json({"type": "spectrum", "sequence": frame["frame_id"], "frame": frame})

    @app.get("/api/spectrum/stream/status")
    def spectrum_stream_status():
        return runtime.spectrum_stream.stats()

    @app.websocket("/api/spectrum/stream")
    async def spectrum_socket(socket: WebSocket):
        # HTTP middleware does not see WebSocket handshakes. Browser streams
        # must originate from this app; local CLI clients may omit Origin.
        from urllib.parse import urlsplit
        origin = socket.headers.get("origin")
        if origin is not None:
            try:
                parsed = urlsplit(origin)
                valid_origin = parsed.scheme in ("http", "https") and parsed.netloc.casefold() == socket.headers.get("host", "").casefold()
            except ValueError:
                valid_origin = False
            if not valid_origin:
                await socket.close(code=1008, reason="Fremder Browser-Ursprung nicht erlaubt")
                return
        try:
            fps = float(socket.query_params.get("max_fps", "15"))
            bins = int(socket.query_params.get("bins", "1024"))
            if not math.isfinite(fps) or not 1 <= fps <= 30 or bins != 1024:
                raise ValueError()
        except (ValueError, TypeError):
            await socket.close(code=1008, reason="max_fps: 1–30; bins: 1024 erforderlich")
            return
        try:
            subscriber = runtime.spectrum_stream.subscribe()
        except ValueError:
            await socket.close(code=1013, reason="Maximal vier Liveanzeigen gleichzeitig")
            return
        try:
            await socket.accept()
            sequence = -1
            while not runtime.closing and not runtime.spectrum_stream.closed:
                # A bounded wait uses a pool thread, not the event loop or the
                # receiver lock. Slow clients only receive the newest frame.
                frame = await asyncio.to_thread(runtime.spectrum_stream.wait_frame, subscriber, sequence, 1.)
                if frame is None:
                    if runtime.spectrum_stream.closed:
                        break
                    await asyncio.wait_for(socket.send_json({"type": "heartbeat", "sequence": sequence}), timeout=2.)
                    continue
                sequence, encoded = frame
                await asyncio.wait_for(socket.send_text(encoded), timeout=2.)
                await asyncio.sleep(1. / fps)
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
            pass
        finally:
            runtime.spectrum_stream.unsubscribe(subscriber)
            try:
                await socket.close()
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass

    @app.get("/api/analysis")
    def analysis_status():
        return finite_json({**runtime.analysis.snapshot(), "scan": runtime.scanner.status()})

    @app.get("/api/analysis/settings")
    def analysis_settings():
        return runtime.analysis.get_settings()

    @app.post("/api/analysis/settings")
    def set_analysis_settings(payload: dict):
        return finite_json(runtime.analysis.set_settings(payload))

    @app.get("/api/analysis/catalogue")
    def analysis_catalogue():
        models = []
        for item in runtime.store.all("model"):
            summary = {key: item.get(key) for key in ("id", "name", "topology_id", "evaluation_available", "validation_status")}
            try:
                metadata = ml.load_model_metadata(item["model_path"])
                summary.update(classes=metadata["classes"], segment_length=metadata["segment_length"],
                               evaluation_available=metadata.get("evaluation_available", False))
            except (ValueError, OSError, KeyError) as exc:
                summary["error"] = str(exc)
            models.append(summary)
        return finite_json({"taxonomy": decision.get_taxonomy(), "models": models,
                            "settings": runtime.analysis.get_settings()})

    @app.get("/api/analysis/signals")
    def analysis_signals(limit: int = 100, session_id: str | None = None):
        if not 1 <= limit <= 2000:
            raise ValueError("Signallimit muss zwischen 1 und 2000 liegen")
        return finite_json({"signals": runtime.analysis.signals.list(limit, session_id, compact=True),
                            "stats": runtime.analysis.signals.stats()})

    def signal_detail(identifier):
        value = runtime.analysis.signals.get(clean_id(identifier))
        if value is None:
            raise HTTPException(404, "Signalentscheidung nicht gefunden")
        return {**value, "history": runtime.analysis.signals.history(identifier, 500),
                "retention": {"history_limit_in_export": 500, **runtime.analysis.signals.stats()}}

    @app.get("/api/analysis/signals/{identifier}")
    def analysis_signal(identifier: str):
        return finite_json(signal_detail(identifier))

    @app.get("/api/analysis/signals/{identifier}/download")
    def download_analysis_signal(identifier: str):
        value = finite_json(signal_detail(identifier))
        return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="signal-{clean_id(identifier)}.json"'})

    @app.post("/api/analysis/recordings/{identifier}")
    def analyze_recording(identifier: str, payload: dict | None = None):
        payload = payload or {}
        if set(payload) - {"model_ids", "explain_models"}:
            raise ValueError("Unbekannte Felder der Aufnahmeauswertung")
        recording = runtime.recording(identifier)
        path = runtime.recording_path(recording)
        iq, metadata = read_recording(path, count=65536)
        glob = metadata.get("global", {})
        captures = metadata.get("captures") or [{}]
        # A recording can contain retunes. Analyze only one consistent prefix.
        boundaries = [int(c["core:sample_start"]) for c in captures if c.get("core:sample_start", 0) > 0]
        if boundaries:
            iq = iq[:min(boundaries)]
        models = None
        if "model_ids" in payload:
            ids = payload["model_ids"]
            if not isinstance(ids, list) or len(ids) > 8 or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
                raise ValueError("Höchstens acht eindeutige Modellkennungen erforderlich")
            models = [{"id": item, "name": runtime.model(item).get("name", item),
                       "model_path": runtime.model(item)["model_path"], "level": "modulation"} for item in ids]
        entry = {"iq": iq, "channel_id": identifier, "session_id": f"recording:{identifier}",
                 "source_epoch": recording.get("group_id", identifier), "origin_group_id": recording.get("group_id"),
                 "source": "recording", "recording_id": identifier,
                 "synthetic": recording.get("synthetic", False),
                 "data_provenance": recording.get("data_provenance", {}),
                 "observed_at": captures[0].get("core:datetime") or utcnow(),
                 "sample_rate": glob.get("core:sample_rate"),
                 "center_frequency_hz": captures[0].get("core:frequency"),
                 "bandwidth_hz": glob.get("sc:selected_bandwidth_hz", glob.get("core:sample_rate")),
                 "sample_count": len(iq), "source_sample_start": 0, "source_sample_stop": len(iq),
                 "observation_id": hashlib.sha256(iq.tobytes()).hexdigest(),
                 "quality": glob.get("sc:integrity", {}),
                 "scope": "consistent_recording_prefix; replay_wideband_for_multisignal_detection"}
        settings = runtime.analysis.get_settings()
        if "explain_models" in payload:
            if type(payload["explain_models"]) is not bool:
                raise ValueError("explain_models muss true oder false sein")
            settings["policy"]["explain_models"] = payload["explain_models"]
        with runtime.analysis.cycle_lock:
            return finite_json(runtime.analysis.analyze_entry(entry, models=models, settings=settings))

    @app.post("/api/analysis/scan/plan")
    def analysis_scan_plan(payload: dict):
        return finite_json(build_scan_plan(payload))

    @app.get("/api/analysis/scan")
    def analysis_scan():
        return finite_json(runtime.scanner.status())

    @app.post("/api/analysis/scan/start")
    def start_analysis_scan(payload: dict):
        with runtime.lock:
            plan = build_scan_plan(payload)
            # Match the decision filter to this requested scan, without changing expert scopes.
            settings = runtime.analysis.get_settings()
            settings.update(start_frequency_hz=plan["requested_range"]["start_frequency_hz"],
                            stop_frequency_hz=plan["requested_range"]["stop_frequency_hz"])
            runtime.analysis.set_settings(settings)
            return finite_json(runtime.scanner.start(payload))

    @app.post("/api/analysis/scan/stop")
    def stop_analysis_scan():
        with runtime.lock:
            return finite_json(runtime.scanner.stop())

    @app.get("/api/live/compare")
    def live_comparison():
        return runtime.live_comparison_settings()

    @app.post("/api/live/compare")
    def set_live_comparison(payload: LiveComparisonInput):
        if len(set(payload.model_ids)) != len(payload.model_ids):
            raise ValueError("Ein Modell darf im Live-Vergleich nur einmal vorkommen")
        for identifier in payload.model_ids:
            model = runtime.model(identifier)
            ml.load_model_metadata(model["model_path"])
        with runtime.lock:
            runtime.live_model_ids = list(payload.model_ids)
            runtime.live_timings = {identifier: value for identifier, value in runtime.live_timings.items() if identifier in payload.model_ids}
        return runtime.live_comparison_settings()

    @app.get("/api/recordings")
    def recordings():
        return finite_json(runtime.recordings())

    @app.post("/api/recordings/labels/batch")
    def batch_labels(payload: BatchLabelInput):
        return finite_json(runtime.batch_labels(payload))

    @app.get("/api/recordings/cleanup-preview")
    def cleanup_recordings_preview(max_duration_seconds: float = 0.5):
        return finite_json(runtime.cleanup_preview(max_duration_seconds))

    @app.post("/api/recordings/delete-preview")
    def selected_recordings_preview(payload: DeleteRecordingsPreviewInput):
        return finite_json(runtime.cleanup_preview(None, payload.recording_ids))

    @app.post("/api/recordings/delete")
    @app.post("/api/recordings/delete-batch")
    def delete_selected_recordings(payload: DeleteRecordingsInput):
        return finite_json(runtime.delete_recordings(payload))

    @app.post("/api/recordings/import")
    async def import_recording(metadata: UploadFile = File(...), data: UploadFile = File(...)):
        identifier = uuid.uuid4().hex
        temp_dir = Path(tempfile.mkdtemp(prefix="import-", dir=directory))
        try:
            meta_path = temp_dir / (identifier + ".sigmf-meta")
            data_path = temp_dir / (identifier + ".sigmf-data")
            await copy_upload(metadata, meta_path, 4 * 1024 * 1024)
            size = await copy_upload(data, data_path, 8 * 1024**3)
            doc = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("global"), dict):
                raise ValueError("SigMF-Metadaten benötigen ein global-Objekt")
            global_meta = doc.get("global", {})
            datatype = global_meta.get("core:datatype")
            sample_bytes = {"cf32_le": 8, "ci16_le": 4}.get(datatype)
            if sample_bytes is None or size == 0 or size % sample_bytes:
                raise ValueError("SigMF: cf32_le oder ci16_le und vollständige IQ-Samples erforderlich")
            if global_meta.get("core:num_channels", 1) != 1:
                raise ValueError("Nur einkanalige IQ-Aufnahmen werden unterstützt")
            global_meta["core:dataset"] = data_path.name
            global_meta["signalclassifier:imported_at"] = utcnow()
            has_session = global_meta.get("signalclassifier:session_id") or global_meta.get("sc:session_id")
            origin_recording = global_meta.get("sc:origin_recording_id") or global_meta.get("signalclassifier:origin_recording_id")
            parent_recording = global_meta.get("sc:parent_recording_id") or global_meta.get("signalclassifier:parent_recording_id")
            declared_recording = global_meta.get("sc:recording_id")
            if not has_session or not (origin_recording or parent_recording or declared_recording):
                with data_path.open("rb") as source_file:
                    fingerprint = hashlib.file_digest(source_file, "sha256").hexdigest()
            if not has_session:
                global_meta["signalclassifier:session_id"] = "sha256-" + fingerprint
            if not origin_recording and not parent_recording:
                global_meta["sc:origin_recording_id"] = "recording:" + str(declared_recording or "sha256-" + fingerprint)
            # Validate bounded data against the reader before committing files.
            meta_path.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            read_recording(meta_path, count=1024)
            for path in (data_path, meta_path):
                shutil.move(str(path), directory / "recordings" / path.name)
            return runtime.recording(identifier)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            await metadata.close()
            await data.close()

    @app.get("/api/recordings/{identifier}/download")
    def download_recording(identifier: str):
        path = runtime.recording_path(runtime.recording(identifier))
        data_path = path.with_suffix(".sigmf-data")
        destination = directory / "exports" / (clean_id(identifier) + ".sigmf.zip")
        with runtime.lock:
            with zipfile.ZipFile(destination.with_suffix(".tmp"), "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(path, path.name)
                archive.write(data_path, data_path.name)
            destination.with_suffix(".tmp").replace(destination)
        return FileResponse(destination, filename=destination.name)

    @app.post("/api/recordings/{identifier}/label")
    def label_recording(identifier: str, payload: LabelInput):
        with runtime.lock:
            recording = runtime.recording(identifier)
            annotation = {**payload.model_dump(), "id": identifier, "recording_id": identifier,
                          "updated_at": utcnow(), "provenance": "human-confirmed" if payload.verified else "human-provisional"}
            return runtime.write_labels([(recording, annotation)])[0]

    @app.post("/api/recordings/{identifier}/classify")
    def classify_recording(identifier: str, payload: dict | None = None):
        payload = payload or {}
        path = runtime.recording_path(runtime.recording(identifier))
        model_path = runtime.model(payload["model_id"])["model_path"] if payload.get("model_id") else runtime.active_model_path()
        length = ml.load_model_metadata(model_path)["segment_length"] if model_path else 16384
        iq, metadata = read_recording(path, count=length)
        glob = metadata.get("global", metadata)
        captures = metadata.get("captures", [{}])
        result = ml.classify(iq, glob.get("core:sample_rate", metadata.get("sample_rate", 1)), center_frequency_hz=captures[0].get("core:frequency"), use_priors=bool(payload.get("use_priors", False)), model_path=model_path, explain=True)
        result["signal_analysis"] = analyze_recording(identifier, {"model_ids": [payload["model_id"]]} if payload.get("model_id") else {})
        return finite_json(result)

    @app.get("/api/emitters")
    def emitters():
        return runtime.store.all("emitter")

    @app.post("/api/emitters")
    def create_emitter(payload: EmitterInput):
        if payload.frequency_max_hz < payload.frequency_min_hz:
            raise ValueError("Obergrenze liegt unter der Untergrenze")
        return runtime.store.put("emitter", uuid.uuid4().hex, {**payload.model_dump(), "verified": False, "created_at": utcnow()})

    @app.get("/api/labels")
    def get_labels():
        return runtime.store.all("label")

    @app.get("/api/labels/suggest")
    def suggest(frequency_hz: float):
        if not math.isfinite(frequency_hz) or not 0 <= frequency_hz <= 100e9:
            raise ValueError("Ungültige Frequenz")
        return {"suggestions": labels.suggest(runtime.store, frequency_hz)}

    @app.get("/api/topologies")
    def topologies():
        return finite_json(runtime.topologies())

    @app.post("/api/topologies")
    def save_topology(payload: dict):
        result = ml.save_topology(payload, directory / "topologies")
        return runtime.store.put("topology", result["id"], finite_json(result))

    @app.get("/api/topologies/{identifier}/download")
    def download_topology(identifier: str):
        clean_id(identifier)
        for value in runtime.topologies():
            if value["id"] == identifier:
                return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="{identifier}.json"'})
        raise HTTPException(404, "Topologie nicht gefunden")

    @app.get("/api/publication/status")
    def publication_status():
        return publication.credential_status()

    @app.get("/api/experiments")
    def experiments():
        return runtime.store.all("experiment")

    @app.post("/api/experiments/validate")
    def validate_dataset(payload: dict):
        try:
            config = validate_experiment(payload, runtime)
            readiness = ml.dataset_readiness(config)
            if config["github_publication"]["enabled"] and not publication.credential_status()["configured"]:
                readiness.setdefault("warnings", []).append("GitHub-Upload aktiviert, aber kein Token in der Backend-Umgebung gesetzt. Das Experiment kann laufen; der Upload bleibt bis zur Einrichtung wiederholbar.")
            return finite_json(readiness)
        except (ValueError, HTTPException, OSError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return {"ready": False, "evaluation_mode": payload.get("evaluation_mode", "independent"),
                    "classes": [], "group_count": 0, "errors": [str(detail)], "warnings": []}

    @app.post("/api/experiments")
    def start_experiment(payload: dict):
        with runtime.lock:
            config = validate_experiment(payload, runtime)
            readiness = ml.dataset_readiness(config)
            if not readiness["ready"]:
                raise ValueError("; ".join(readiness["errors"]))
            if any(e.get("status") in ("queued", "running", "cancelling") for e in runtime.store.all("experiment")):
                raise HTTPException(409, "Es läuft bereits ein Experiment")
            identifier = uuid.uuid4().hex
            provenance = runtime.freeze_experiment_inputs(identifier, config)
            state = runtime.store.put("experiment", identifier, {"name": config.get("name") or "AM / FM", "status": "queued", "config": config, "progress": 0, "created_at": utcnow(),
                "publication": {"status": "queued" if config["github_publication"]["enabled"] else "disabled",
                                "repository": config["github_publication"]["repository"], "branch": config["github_publication"]["branch"]},
                "provenance": provenance, "log_files": {"events": "events.jsonl", "configuration": "configuration.json", "inventory": "dataset-inventory.json", "provenance": "provenance.json"}})
            runtime.log_event(identifier, "queued", {"config": config, "provenance": provenance})
            runtime.cancel_events[identifier] = threading.Event()
            runtime.executor.submit(runtime.run, identifier, config)
        return state

    @app.get("/api/experiments/{identifier}")
    def experiment(identifier: str):
        value = runtime.store.get("experiment", clean_id(identifier))
        if value is None:
            raise HTTPException(404, "Experiment nicht gefunden")
        if value.get("status") in ("cancelled", "failed", "interrupted"):
            value["partial_reports"] = runtime.partial_reports(identifier)
        return value

    @app.get("/api/experiments/{identifier}/download")
    def download_experiment(identifier: str):
        value = experiment(identifier)
        return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="experiment-{identifier}.json"'})

    @app.get("/api/experiments/{identifier}/paper-bundle")
    def download_paper_bundle(identifier: str):
        files = runtime.paper_package(clean_id(identifier))
        destination = directory / "exports" / f"{identifier}.{uuid.uuid4().hex}.paper.zip"
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
        return FileResponse(destination, filename=f"experiment-{identifier}-paper.zip", media_type="application/zip")

    @app.post("/api/experiments/{identifier}/publish")
    def publish_experiment(identifier: str):
        return finite_json(runtime.queue_publication(clean_id(identifier)))

    @app.get("/api/experiments/{identifier}/report-bundle")
    def download_report_bundle(identifier: str):
        from .reporting import build_report_bundle
        with runtime.lock:
            value = experiment(identifier)
            folder = directory / "experiments" / identifier
            inventory_path = folder / "dataset-inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.is_file() else []
            value["partial_reports"] = runtime.partial_reports(identifier)
            value["export_snapshot"] = {"timestamp_utc": utcnow(), "status": value.get("status"),
                "complete": value.get("status") == "completed", "inventory_available": inventory_path.is_file(),
                "legacy_warning": None if value.get("provenance") else "Historical run has no queue-time provenance snapshot; no current labels or hardware have been substituted."}
            events = experiment_log.read_events(folder / "events.jsonl")
        destination = directory / "exports" / f"{identifier}.{uuid.uuid4().hex}.report.zip"
        build_report_bundle(value, destination, events=events, dataset_inventory=inventory)
        return FileResponse(destination, filename=f"experiment-{identifier}-report.zip", media_type="application/zip")

    @app.post("/api/experiments/{identifier}/cancel")
    def cancel_experiment(identifier: str):
        with runtime.lock:
            value = experiment(identifier)
            if value["status"] not in ("queued", "running", "cancelling"):
                return value
            runtime.cancel_events[identifier].set()
            value["status"] = "cancelling"
            runtime.log_event(identifier, "cancel_requested", {"status": value["status"]})
            return runtime.store.put("experiment", identifier, value)

    @app.get("/api/models")
    def models():
        return runtime.store.all("model")

    @app.get("/api/models/{identifier}/download")
    def download_model(identifier: str):
        value = runtime.model(identifier)
        source = Path(value["model_path"])
        destination = directory / "exports" / (identifier + ".model.zip")
        with runtime.lock:
            with zipfile.ZipFile(destination.with_suffix(".tmp"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(source.rglob("*")):
                    if path.is_file() and not path.is_symlink():
                        archive.write(path, path.relative_to(source).as_posix())
            destination.with_suffix(".tmp").replace(destination)
        return FileResponse(destination, filename=destination.name)

    @app.post("/api/models/import")
    async def import_model(file: UploadFile = File(...)):
        identifier = uuid.uuid4().hex
        temporary = Path(tempfile.mkdtemp(prefix="model-import-", dir=directory))
        try:
            archive_path = temporary / "upload.zip"
            await copy_upload(file, archive_path, 512 * 1024**2)
            unpacked = temporary / "unpacked"
            unpacked.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                items = archive.infolist()
                if len(items) > 50 or sum(item.file_size for item in items) > 1024**3:
                    raise ValueError("Modellarchiv überschreitet die Größenbegrenzung")
                allowed = {"model.json", "topology.json", "report.json", "weights.npz", "weights.pt", "preprocessing.json", "normalization.npz"}
                for item in items:
                    if item.filename not in allowed or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Modellarchiv enthält nicht unterstützte Dateien")
                    with archive.open(item) as incoming, (unpacked / item.filename).open("wb") as output:
                        shutil.copyfileobj(incoming, output, length=1024 * 1024)
            metadata = ml.validate_model(unpacked)
            report = {}
            report_path = unpacked / "report.json"
            if report_path.is_file():
                if report_path.stat().st_size > 16 * 1024**2:
                    raise ValueError("Modellbericht überschreitet 16 MiB")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if not isinstance(report, dict):
                    raise ValueError("Modellbericht muss ein JSON-Objekt sein")
            destination = directory / "models" / identifier
            shutil.move(str(unpacked), destination)
            return runtime.store.put("model", identifier, finite_json({**metadata,
                "name": metadata.get("name") or metadata.get("topology", {}).get("name") or "Importiertes Modell",
                "model_path": str(destination), "created_at": utcnow(), "imported": True,
                "metrics": report.get("metrics", {}), "score": report.get("score"), "assessment_hardware": report.get("hardware")}))
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ValueError(f"Ungültiges Modellarchiv: {exc}") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            await file.close()

    @app.post("/api/models/{identifier}/activate")
    def activate_model(identifier: str):
        model = runtime.model(identifier)
        ml.load_model_metadata(model["model_path"])
        return runtime.store.put("active_model", "main", {"model_id": identifier, "name": model.get("name"), "activated_at": utcnow()})

    @app.get("/api/benchmark")
    def benchmark(refresh: bool = False):
        with runtime.lock:
            if runtime.benchmark is None or refresh:
                receiver = runtime.capture.status()
                context = {"receiver_running": bool(receiver.get("busy")),
                           "classification_enabled": runtime.analysis.classification_enabled(),
                           "experiment_running": any(e.get("status") in ("queued", "running", "cancelling")
                                                     for e in runtime.store.all("experiment")),
                           "external_host_load_verified": False}
                runtime.benchmark = finite_json({**ml.benchmark_hardware(), "measured_at": utcnow(),
                    "observation_context": context,
                    "measurement_scope": "Fixed CPU feature extraction on 4096 synthetic samples; median of 12 calls after 3 warmups. Not a GPU benchmark or an isolated whole-host measurement."})
            return runtime.benchmark

    @app.get("/api/drones/capabilities")
    def drone_capabilities():
        return capabilities()

    @app.get("/api/drones/tracks")
    def tracks():
        return finite_json(runtime.drones.tracks())

    @app.post("/api/drones/ingest")
    def ingest(payload: dict):
        return finite_json(runtime.drones.ingest(payload))

    @app.get("/api/drones/example")
    def drone_example():
        return example_payload()

    @app.get("/api/drones/bridge")
    def bridge_status():
        return runtime.bridge.status()

    @app.post("/api/drones/bridge/start")
    def bridge_start(payload: dict | None = None):
        return runtime.bridge.start(payload or {})

    @app.post("/api/drones/bridge/stop")
    def bridge_stop():
        return runtime.bridge.stop()

    @app.get("/api/settings")
    def settings():
        return runtime.store.get("settings", "main")

    @app.put("/api/settings")
    def save_settings(payload: SettingsInput):
        return runtime.store.put("settings", "main", payload.model_dump())

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app


def hardware_info():
    import importlib.util
    import platform
    import psutil
    result = {"platform": platform.platform(), "cpu": platform.processor() or platform.machine(), "logical_cpus": os.cpu_count(),
              "ram_bytes": psutil.virtual_memory().total, "torch_available": bool(importlib.util.find_spec("torch")),
              "pluto_driver": bool(importlib.util.find_spec("adi")), "rsp1a_driver": bool(importlib.util.find_spec("SoapySDR"))}
    # Importing torch on every status poll is expensive. GPU details are in benchmark.
    return result
