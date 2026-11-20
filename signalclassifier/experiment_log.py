"""Frozen experiment provenance and append-only structured run events."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path

from . import __version__
from .db import utcnow
from .sigmf import recording_info

MAX_INVENTORY_METADATA_BYTES = 32 * 1024**2


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def software_provenance() -> dict:
    package = Path(__file__).resolve().parent
    files = {path.name: file_sha256(path) for path in sorted(package.glob("*.py")) if not path.is_symlink()}
    packages = {}
    for name in ("numpy", "scipy", "torch", "fastapi", "uvicorn", "psutil", "python-multipart"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    revision, dirty = None, None
    if (package.parent / ".git").exists():
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package.parent, capture_output=True, text=True, check=True, timeout=2).stdout.strip()
            dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=package.parent, capture_output=True, text=True, check=True, timeout=2).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return {"application": "SignalClassifier", "version": __version__, "git_revision": revision,
            "repository": "https://github.com/schildtaero/SignalClassifier",
            "working_tree_dirty": dirty, "python": platform.python_version(), "dependencies": packages,
            "source_files_sha256": files,
            "source_hash_scope": "Python source files directly in the backend package; frontend assets and launcher scripts are outside this fingerprint.",
            "source_fingerprint_sha256": canonical_sha256(files)}


def dataset_inventory(entries: list[dict], annotations: dict[str, dict]) -> list[dict]:
    """Snapshot metadata and labels, without reading or claiming to hash full IQ."""
    rows, metadata_bytes = [], 0
    for entry in entries:
        path = Path(entry.get("metadata_path") or entry["path"])
        info = recording_info(path)
        data = Path(info["data_path"])
        stat = data.stat()
        metadata = info["metadata"]
        glob = metadata["global"]
        identifier = str(entry.get("id") or info["id"])
        annotation = annotations.get(identifier) or {
            "label": entry.get("label"), "verified": entry.get("verified") is True,
            "notes": glob.get("signalclassifier:label_notes", ""),
            "provenance": glob.get("signalclassifier:label_provenance", "sigmf-import"),
            "suggestion": glob.get("signalclassifier:label_suggestion")}
        size = path.stat().st_size
        include_metadata = metadata_bytes + size <= MAX_INVENTORY_METADATA_BYTES
        metadata_bytes += size if include_metadata else 0
        rows.append({"id": identifier, "label": entry.get("label"), "verified": entry.get("verified") is True,
                     "group_id": entry.get("group_id") or info["group_id"], "annotation": annotation,
                     "metadata_filename": path.name, "data_filename": data.name,
                     "source": info["source"], "mode": info["mode"], "synthetic": info["synthetic"],
                     "sample_rate": info["sample_rate"], "center_frequency_hz": info["center_frequency_hz"],
                     "sample_count": info["sample_count"], "duration_seconds": info["duration_seconds"],
                     "datatype": info["datatype"], "data_bytes": stat.st_size,
                     "capture_integrity": glob.get("sc:integrity"), "capture_performance": glob.get("sc:performance"),
                     "metadata_sha256": file_sha256(path),
                     "iq_file_stat": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns},
                     "iq_full_content_sha256": None,
                     "hash_scope": "Metadata bytes hashed; IQ stat is a change hint, not an IQ content hash. Used-segment hashes are in model reports.",
                     "sigmf_metadata": metadata if include_metadata else None,
                     "metadata_omitted_reason": None if include_metadata else "Full SigMF metadata exceeds the 32-MiB inventory budget; retain original sidecar. Required RF/label/group fields and metadata SHA-256 remain recorded."})
    return rows


def read_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for index, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("event must be an object")
                rows.append(value)
            except (ValueError, TypeError):
                rows.append({"event": "journal_read_warning", "line": index,
                             "data": {"reason": "Incomplete or invalid journal line retained as a visible warning."}})
    return rows


def append_event(path: Path, sequence: int, event: str, data: dict, elapsed_seconds=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"schema_version": 1, "sequence": sequence, "timestamp_utc": utcnow(),
           "event": event, "elapsed_seconds": elapsed_seconds, "data": data}
    with path.open("ab+") as stream:
        stream.seek(0, 2)
        if stream.tell():
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                # Preserve a crash-truncated record as its own visible warning.
                stream.write(b"\n")
        stream.write((json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        stream.flush()
