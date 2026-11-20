"""Small, bounded SigMF reader and streaming writer for single-channel complex IQ.

Metadata is JSON and data are numeric arrays; no Python object deserialization is
used. Only the explicitly supported, contiguous cf32_le and ci16_le formats are
accepted. See docs/aufnahme.md for the intentionally unsupported SigMF variants.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_READ_SAMPLES = 16 * 1024 * 1024
FORMATS = {"cf32_le": (np.dtype("<c8"), 8), "ci16_le": (np.dtype("<i2"), 4)}


def _finite_number(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} muss eine Zahl sein.")
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"Ungültiger Wert für {name}.")
    return value


def _sample_index(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} muss eine nichtnegative Ganzzahl sein.")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"Ungültiger JSON-Zahlenwert: {value}")


def paired_data_path(meta_path: str | Path, metadata: dict | None = None) -> Path:
    """Resolve a local sibling dataset only; reject traversal and symlinks."""
    path = Path(meta_path)
    if path.suffix != ".sigmf-meta":
        raise ValueError("Eine .sigmf-meta-Datei ist erforderlich.")
    dataset = (metadata or {}).get("global", {}).get("core:dataset")
    expected = path.with_suffix(".sigmf-data")
    if dataset is not None:
        if not isinstance(dataset, str) or dataset != expected.name:
            raise ValueError("Nur gleichnamige .sigmf-data-Dateien werden unterstützt.")
    if path.is_symlink() or expected.is_symlink():
        raise ValueError("Symlinks sind für importierte SigMF-Aufnahmen nicht zulässig.")
    return expected


def read_metadata(meta_path: str | Path) -> dict:
    """Validate supported structure without loading the IQ data into RAM."""
    path = Path(meta_path)
    if path.is_symlink():
        raise ValueError("SigMF-Metadaten dürfen kein Symlink sein.")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("SigMF-Metadaten sind größer als 4 MiB.")
    metadata = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if not isinstance(metadata, dict):
        raise ValueError("SigMF-Metadaten müssen ein JSON-Objekt sein.")
    global_meta = metadata.get("global")
    if not isinstance(global_meta, dict):
        raise ValueError("SigMF-Feld global fehlt.")
    if global_meta.get("core:datatype") not in FORMATS:
        raise ValueError("Unterstützt werden nur cf32_le und ci16_le.")
    if not isinstance(global_meta.get("core:version"), str):
        raise ValueError("SigMF core:version fehlt.")
    rate = _finite_number(global_meta.get("core:sample_rate"), "core:sample_rate")
    if rate <= 0 or rate > 1e9:
        raise ValueError("Abtastrate muss > 0 und <= 1 GS/s sein.")
    if global_meta.get("core:num_channels", 1) != 1:
        raise ValueError("Nur einkanalige komplexe IQ-Aufnahmen werden unterstützt.")
    # Non-contiguous headers, per-capture rate changes and byte offsets need a
    # different index mapping and must never be silently interpreted as IQ.
    if global_meta.get("core:trailing_bytes", 0):
        raise ValueError("Datasets mit angehängten Fremddaten werden nicht unterstützt.")
    captures = metadata.get("captures")
    annotations = metadata.get("annotations")
    if not isinstance(captures, list) or not captures or len(captures) > 10000:
        raise ValueError("Mindestens ein SigMF-Capture ist erforderlich (maximal 10000).")
    if not isinstance(annotations, list) or len(annotations) > 100000:
        raise ValueError("SigMF annotations muss eine Liste mit maximal 100000 Einträgen sein.")
    previous = -1
    for capture in captures:
        if not isinstance(capture, dict):
            raise ValueError("Ungültiger SigMF-Capture.")
        start = _sample_index(capture.get("core:sample_start"), "core:sample_start")
        if start <= previous or (previous == -1 and start != 0):
            raise ValueError("Captures müssen bei Sample 0 beginnen und aufsteigend sein.")
        previous = start
        _finite_number(capture.get("core:frequency", 0), "core:frequency")
        if capture.get("core:header_bytes", 0):
            raise ValueError("Capture-Header im IQ-Dataset werden nicht unterstützt.")
        if "core:sample_rate" in capture:
            raise ValueError("Abtastratenänderungen innerhalb einer Aufnahme werden nicht unterstützt.")
    for annotation in annotations:
        if not isinstance(annotation, dict):
            raise ValueError("Ungültige SigMF-Annotation.")
        _sample_index(annotation.get("core:sample_start"), "Annotation core:sample_start")
        if "core:sample_count" in annotation:
            _sample_index(annotation["core:sample_count"], "Annotation core:sample_count")
    data_path = paired_data_path(path, metadata)
    size = data_path.stat().st_size
    sample_bytes = FORMATS[global_meta["core:datatype"]][1]
    if size % sample_bytes:
        raise ValueError("SigMF-Dataset endet innerhalb eines IQ-Samples.")
    total = size // sample_bytes
    for capture in captures:
        if capture["core:sample_start"] > total:
            raise ValueError("Capture-Index liegt außerhalb des Datasets.")
    for annotation in annotations:
        start = annotation["core:sample_start"]
        count = annotation.get("core:sample_count", 0)
        if start > total or start + count > total:
            raise ValueError("Annotation liegt außerhalb des Datasets.")
    return metadata


def recording_info(meta_path: str | Path) -> dict:
    path = Path(meta_path)
    metadata = read_metadata(path)
    global_meta = metadata["global"]
    data_path = paired_data_path(path, metadata)
    size = data_path.stat().st_size
    count = size // FORMATS[global_meta["core:datatype"]][1]
    rate = float(global_meta["core:sample_rate"])
    captures = metadata["captures"]
    return {
        "id": path.stem,
        "name": global_meta.get("core:description") or path.stem,
        "metadata_path": str(path),
        "data_path": str(data_path),
        "sample_rate": rate,
        "center_frequency_hz": float(captures[0].get("core:frequency", 0)),
        "samples": count,
        "sample_count": count,
        "duration_seconds": count / rate,
        "bytes": size,
        "size_bytes": size,
        "datatype": global_meta["core:datatype"],
        "created_at": captures[0].get("core:datetime"),
        "source": global_meta.get("sc:source", "import"),
        "mode": global_meta.get("sc:mode", "import"),
        "synthetic": global_meta.get("sc:source") == "synthetic",
        "group_id": (global_meta.get("signalclassifier:group_id") or global_meta.get("sc:origin_group_id")
                     or global_meta.get("signalclassifier:session_id") or global_meta.get("sc:session_id") or path.stem),
        "metadata": metadata,
    }


def read_recording(meta_path: str | Path, start: int = 0, count: int | None = None) -> tuple[np.ndarray, dict]:
    """Read a bounded segment. ci16 is normalized by 32768 for I and Q.

    If count is omitted, at most 16 Mi complex samples may be requested. Larger
    datasets must be streamed by explicit start/count calls, preventing accidental
    multi-gigabyte RAM allocations on the laptop.
    """
    metadata = read_metadata(meta_path)
    start = _sample_index(start, "start")
    data_path = paired_data_path(meta_path, metadata)
    dtype, bytes_per_sample = FORMATS[metadata["global"]["core:datatype"]]
    total = data_path.stat().st_size // bytes_per_sample
    if start > total:
        raise ValueError("Startsample liegt hinter dem Ende der Aufnahme.")
    if count is None:
        count = total - start
    count = _sample_index(count, "count")
    if count > MAX_READ_SAMPLES:
        raise ValueError("Lesezugriff ist auf 16 Mi IQ-Samples begrenzt; bitte segmentweise lesen.")
    count = min(count, total - start)
    with data_path.open("rb") as handle:
        handle.seek(start * bytes_per_sample)
        raw = np.fromfile(handle, dtype=dtype, count=count if bytes_per_sample == 8 else count * 2)
    if bytes_per_sample == 4:
        raw = raw.astype(np.float32).view(np.complex64) / np.float32(32768.0)
    if not np.isfinite(raw).all():
        raise ValueError("IQ-Segment enthält NaN oder unendliche Werte.")
    return np.asarray(raw, dtype=np.complex64), metadata


class SigMFWriter:
    """Append-only numeric writer; publish metadata only after a successful close."""

    def __init__(self, base_path: str | Path, sample_rate: float, center_frequency: float,
                 annotations: list | None = None, extra: dict | None = None):
        self.base_path = Path(base_path)
        if self.base_path.suffix in (".sigmf-meta", ".sigmf-data"):
            self.base_path = self.base_path.with_suffix("")
        self.meta_path = Path(str(self.base_path) + ".sigmf-meta")
        self.data_path = Path(str(self.base_path) + ".sigmf-data")
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        rate = _finite_number(sample_rate, "sample_rate")
        if rate <= 0:
            raise ValueError("Abtastrate muss positiv sein.")
        frequency = _finite_number(center_frequency, "center_frequency")
        self.metadata = {
            "global": {"core:datatype": "cf32_le", "core:sample_rate": rate,
                       "core:version": "1.2.6", "core:recorder": "SignalClassifier",
                       "core:num_channels": 1,
                       "core:extensions": [{"name": "sc", "version": "1.0.0", "optional": True},
                                           {"name": "signalclassifier", "version": "1.0.0", "optional": True}]},
            "captures": [{"core:sample_start": 0, "core:frequency": frequency,
                          "core:datetime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}],
            "annotations": list(annotations or []),
        }
        for key, value in (extra or {}).items():
            if key.startswith("core:"):
                # Prevent callers overwriting format/indexing invariants.
                if key in {"core:description", "core:hw", "core:author", "core:license"}:
                    self.metadata["global"][key] = value
            else:
                self.metadata["global"][key if ":" in key else f"sc:{key}"] = value
        json.dumps(self.metadata, allow_nan=False)
        self._handle = self.data_path.open("xb")
        self.samples = 0
        self.closed = False

    @property
    def bytes_written(self) -> int:
        return self.samples * 8

    def append(self, iq: np.ndarray) -> int:
        if self.closed:
            raise ValueError("Aufnahme ist bereits geschlossen.")
        data = np.asarray(iq, dtype="<c8")
        if data.ndim != 1 or not np.isfinite(data).all():
            raise ValueError("IQ-Daten müssen ein endliches eindimensionales komplexes Array sein.")
        data.tofile(self._handle)
        self.samples += len(data)
        return data.nbytes

    def close(self, extra: dict | None = None) -> Path:
        if self.closed:
            return self.meta_path
        self._handle.flush()
        self._handle.close()
        self.closed = True
        for key, value in (extra or {}).items():
            self.metadata["global"][key if ":" in key else f"sc:{key}"] = value
        self.metadata["global"]["sc:sample_count"] = self.samples
        temp = self.meta_path.with_suffix(".sigmf-meta.tmp")
        temp.write_text(json.dumps(self.metadata, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temp, self.meta_path)
        return self.meta_path


def write_recording(base_path: str | Path, iq: np.ndarray, sample_rate: float,
                    center_frequency: float, annotations: list | None = None,
                    extra: dict | None = None) -> Path:
    writer = SigMFWriter(base_path, sample_rate, center_frequency, annotations, extra)
    try:
        writer.append(iq)
    finally:
        writer.close()
    return writer.meta_path
