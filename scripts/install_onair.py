"""Install an unmodified, checksum-pinned optional ON-AIR portable release.

The generator adapter executes this third-party program as a separate process.
All vendor libraries and licence notices stay inside its private install tree.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid

RELEASE = "4.6.0"
SHA256 = "684de24a747837007c1c9ce8fd751c7d7c6a32fb946d0b79c888c959a044ad0c"
URL = f"https://github.com/adswill/OnAir/releases/download/v{RELEASE}/onair-{RELEASE}-linux-x86_64-portable.tar.gz"
ROOT_NAME = f"onair-{RELEASE}-linux-x86_64-portable"
MAX_DOWNLOAD = 220*1024**2
MAX_EXTRACTED = 2*1024**3


def _inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Unzulässiger Pfad im OnAir-Archiv.")
    return resolved


def safe_extract(archive, root):
    root = Path(root)
    links = []
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 50000 or sum(item.size for item in members) > MAX_EXTRACTED:
            raise ValueError("OnAir-Archiv überschreitet das Entpackbudget.")
        seen = set()
        for item in members:
            path = Path(item.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != ROOT_NAME:
                raise ValueError("OnAir-Archiv enthält einen unerwarteten Pfad.")
            destination = _inside(root, root / path)
            if str(path) in seen:
                raise ValueError("OnAir-Archiv enthält doppelte Pfade.")
            seen.add(str(path))
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif item.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(item)
                if stream is None:
                    raise ValueError("Datei im OnAir-Archiv nicht lesbar.")
                with stream, destination.open("xb") as output:
                    shutil.copyfileobj(stream, output, 1024*1024)
                destination.chmod(0o755 if item.mode & 0o111 else 0o644)
            elif item.issym() or item.islnk():
                links.append((item, destination))
            else:
                raise ValueError("OnAir-Archiv enthält einen nicht unterstützten Dateityp.")
        # Links are created last, so regular extraction never traverses one.
        for item, destination in links:
            target = Path(item.linkname)
            if target.is_absolute():
                raise ValueError("Absoluter Link im OnAir-Archiv.")
            resolved = _inside(root, (destination.parent if item.issym() else root) / target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if item.issym():
                destination.symlink_to(item.linkname)
            elif resolved.is_file():
                os.link(resolved, destination)
            else:
                raise ValueError("Ungültiger Hardlink im OnAir-Archiv.")
        for _, destination in links:
            _inside(root, destination)
    binary = root / ROOT_NAME / "bin" / "onair-cli"
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise ValueError("Das geprüfte OnAir-Archiv enthält keine ausführbare onair-cli.")


def install_archive(archive, destination, *, expected_checksum=SHA256):
    archive, destination = Path(archive), Path(destination)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_checksum:
            raise ValueError("OnAir-Prüfsumme stimmt nicht; nichts installiert.")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".onair-install-", dir=destination) as staging:
        staging = Path(staging)
        safe_extract(archive, staging)
        version = destination / f"{RELEASE}-{uuid.uuid4().hex[:8]}"
        staging.rename(version)
        install = version / ROOT_NAME
        (version / "installation.json").write_text(json.dumps({"release": RELEASE, "url": URL,
            "archive_sha256": expected_checksum, "installed_at_utc": datetime.now(timezone.utc).isoformat(),
            "vendor_contents": "unmodified portable release including libraries and licence notices"}, indent=2)+"\n")
        temporary_link = destination / (".current-"+uuid.uuid4().hex)
        try:
            temporary_link.symlink_to(install.relative_to(destination), target_is_directory=True)
            temporary_link.replace(destination / "current")
        finally:
            temporary_link.unlink(missing_ok=True)
    return install / "bin" / "onair-cli"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optionalen OnAir-IQ-Generator lokal installieren (Linux/WSL x86_64).")
    parser.add_argument("--check", action="store_true", help="Nur die verwaltete Installation prüfen, nichts herunterladen")
    args = parser.parse_args(argv)
    destination = Path.home() / ".local" / "share" / "signalclassifier" / "onair"
    binary = destination / "current" / "bin" / "onair-cli"
    if args.check:
        ready = binary.is_file() and os.access(binary, os.X_OK)
        print("OnAir-CLI vorhanden." if ready else "OnAir-CLI nicht installiert; native Generatoren bleiben verfügbar.")
        return 0 if ready else 1
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        parser.error("Dieser geprüfte Installer unterstützt Linux/WSL x86_64; andere Systeme benötigen eine eigene OnAir-CLI-Installation.")
    print(f"Lade optionale OnAir-CLI {RELEASE} mit Bibliotheken (ca. 187 MB). Kein Sender wird gestartet.", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="signalclassifier-onair-download-") as folder:
            archive = Path(folder) / "onair.tar.gz"
            request = urllib.request.Request(URL, headers={"User-Agent": "SignalClassifier-OnAir-Installer/1"})
            size = 0
            with urllib.request.urlopen(request, timeout=30) as response, archive.open("wb") as output:
                while block := response.read(1024*1024):
                    size += len(block)
                    if size > MAX_DOWNLOAD:
                        raise ValueError("OnAir-Download überschreitet das Größenbudget.")
                    output.write(block)
            installed = install_archive(archive, destination)
        print(f"OnAir-CLI installiert: {installed}")
        print("In SignalClassifier den Signalgenerator-Katalog neu laden. Kein Upload und keine HF-Aussendung ausgeführt.")
        return 0
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"OnAir-Installation fehlgeschlagen: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
