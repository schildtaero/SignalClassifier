"""Bounded, optional use of the separately installed ON-AIR IQ exporter.

No ON-AIR implementation is included here. The shipped registry records facts
from release 4.6.0; an independently configured executable is not authenticated
or guaranteed to implement that release. Generated fixtures are not RF captures
or evidence of protocol conformance.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

import numpy as np


SOURCE_COMMIT = "2f35f509d540ee3b4157affe0a2ce0f9a01a6859"
SOURCE_RELEASE = "4.6.0"
SOURCE_URL = f"https://github.com/adswill/OnAir/tree/{SOURCE_COMMIT}"
MAX_EXPORT_BYTES = 128 * 1024 * 1024
MAX_RETURN_SAMPLES = 4_194_304
MAX_LOG_BYTES = 65_536
_REGISTRY_PATH = Path(__file__).with_name("data") / "onair-generators.json"


class OnAirError(ValueError):
    """An unavailable, invalid, cancelled, or incomplete external export."""


def _registry():
    data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    if data.get("source_commit") != SOURCE_COMMIT:
        raise OnAirError("ON-AIR-Generatorregister hat eine unerwartete Quellversion.")
    return data


def _binary():
    configured = os.environ.get("SIGNALCLASSIFIER_ONAIR_CLI")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        return None
    managed = Path.home() / ".local/share/signalclassifier/onair/current/bin/onair-cli"
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed.resolve())
    return shutil.which("onair-cli") or shutil.which("dect2cli")


def _process_environment(binary):
    env = {**os.environ, "DECT2_DEBUG": "1"}
    # Only the dedicated managed installation owns adjacent library folders.
    # A custom executable must not implicitly load arbitrary sibling libraries.
    base = (Path.home() / ".local/share/signalclassifier/onair").resolve()
    executable = Path(binary).resolve()
    portable = executable.parent.parent
    if (executable.name == "onair-cli" and executable.parent.name == "bin"
            and portable != base and portable.is_relative_to(base)):
        libraries = []
        for relative in ("lib/ffmpeg", "lib"):
            library = portable / relative
            if library.is_dir() and library.resolve().is_relative_to(portable):
                libraries.append(str(library.resolve()))
        if libraries:
            previous = env.get("LD_LIBRARY_PATH")
            env["LD_LIBRARY_PATH"] = ":".join(libraries + ([previous] if previous else []))
    return env


def catalogue():
    """Return registered generators, without launching an executable or radio."""
    available = _binary() is not None
    result = copy.deepcopy(_registry()["generators"])
    for entry in result:
        entry.update(implementation_kind="external_fixture", available=available,
                     reason=None if available else "ON-AIR CLI ist nicht installiert oder nicht ausführbar.")
    return result


def capabilities():
    return {"engine": "onair", "available": _binary() is not None,
            "source_release": SOURCE_RELEASE, "source_commit": SOURCE_COMMIT,
            "executable_version_verified": False, "source_url": SOURCE_URL,
            "license": "GPL-3.0-or-later with section-7(b) attribution; external executable",
            "native_seed_control": False, "native_quantization_bits": 8,
            "max_export_bytes": MAX_EXPORT_BYTES, "max_return_samples": MAX_RETURN_SAMPLES,
            "limitations": ["Empfangsmodi sind keine Liste vollständiger Sendegeneratoren.",
                            "Feste Testinhalte; kein allgemeiner Payload-Seed über die CLI.",
                            "Die native Exportdauer ist ungenau; Ausschnitte sind keine vollständigen Protokollzyklen.",
                            "CF32 speichert intern auf 8-Bit-Stufen quantisierte Generatorwerte.",
                            "Die CLI-Versionsausgabe identifiziert den installierten Release nicht zuverlässig."],
            "generators": catalogue()}


def _number(value, name, lower, upper, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not lower <= value <= upper
            or (integer and type(value) is not int)):
        raise OnAirError(f"{name}: {'ganze Zahl' if integer else 'Zahl'} zwischen {lower} und {upper} erforderlich.")
    return int(value) if integer else float(value)


def _prepare(spec):
    if not isinstance(spec, dict):
        raise OnAirError("ON-AIR-Spezifikation muss ein Objekt sein.")
    unknown = set(spec) - {"mode_id", "sample_rate", "sample_count", "parameters", "seed"}
    if unknown:
        raise OnAirError("Unbekannte ON-AIR-Spezifikationsfelder.")
    mode = spec.get("mode_id")
    entry = next((item for item in _registry()["generators"] if item["id"] == mode), None)
    if entry is None:
        raise OnAirError("Unbekannter oder nicht belegter ON-AIR-Generator.")
    params = spec.get("parameters", {})
    if not isinstance(params, dict):
        raise OnAirError("ON-AIR-Parameter müssen ein Objekt sein.")
    schemas = entry.get("parameters", {})
    if set(params) - set(schemas):
        raise OnAirError("Dieser ON-AIR-Generator unterstützt die angeforderten Parameter nicht.")
    effective = {}
    for name, schema in schemas.items():
        value = params.get(name, schema.get("default"))
        if value is None:
            continue
        effective[name] = _number(value, name, schema["minimum"], schema["maximum"], schema["type"] == "integer")
        if "enum" in schema and effective[name] not in schema["enum"]:
            raise OnAirError(f"{name}: nicht unterstützter Optionswert.")
    if "sample_rate" in spec:
        rate = _number(spec["sample_rate"], "sample_rate", 1, 1e9)
        if entry.get("sample_rate") is None or not math.isclose(rate, entry["sample_rate"], rel_tol=1e-9, abs_tol=.01):
            raise OnAirError("Die ON-AIR CLI kann die native Generatorrate nicht mit --rate einstellen.")
    count = _number(spec["sample_count"], "sample_count", 1, MAX_RETURN_SAMPLES, True) if "sample_count" in spec else None
    seed = _number(spec["seed"], "seed", 0, 2**32 - 1, True) if spec.get("seed") is not None else None
    return entry, effective, count, seed


def normalize_spec(spec):
    """Validate a planned export without filesystem writes or a subprocess."""
    entry, parameters, count, seed = _prepare(spec)
    normalized = {"mode_id": entry["id"], "sample_rate": entry["sample_rate"],
                  "parameters": parameters}
    if count is not None:
        normalized["sample_count"] = count
    if seed is not None:
        normalized["seed"] = seed
    return normalized


def _stop_process(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def _export(argv, output, stop_event, timeout):
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   cwd=output.parent, env=_process_environment(argv[0]))
    except OSError as exc:
        raise OnAirError("Start des ON-AIR-Exporters fehlgeschlagen.") from exc
    tail = bytearray()
    begin = time.monotonic()
    os.set_blocking(process.stdout.fileno(), False)

    def drain():
        for _ in range(8):
            try:
                chunk = os.read(process.stdout.fileno(), 8192)
            except BlockingIOError:
                break
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > MAX_LOG_BYTES:
                del tail[:-MAX_LOG_BYTES]

    try:
        while True:
            drain()
            if stop_event is not None and stop_event.is_set():
                raise OnAirError("ON-AIR-Export abgebrochen.")
            if time.monotonic() - begin >= timeout:
                raise OnAirError("Zeitlimit des ON-AIR-Exports überschritten.")
            if output.exists() and output.stat().st_size > MAX_EXPORT_BYTES:
                raise OnAirError("ON-AIR-Export überschreitet das 128-MiB-Limit.")
            status = process.poll()
            if status is not None:
                drain()
                if status:
                    raise OnAirError(f"ON-AIR-Export fehlgeschlagen (Exitcode {status}).")
                break
            time.sleep(.02)
    finally:
        _stop_process(process)
        drain()
        process.stdout.close()
    text = tail.decode("utf-8", errors="replace")
    if "no built-in test signal" in text.lower() or "no synthetic signal is made" in text.lower():
        raise OnAirError("Die installierte ON-AIR-Version hat keinen Generator für diesen Modus.")
    return text, time.monotonic() - begin


def _read_export(path, entry, requested_count):
    metadata_path = path.with_suffix(".sigmf-meta")
    if path.is_symlink() or metadata_path.is_symlink() or not path.is_file() or not metadata_path.is_file():
        raise OnAirError("ON-AIR hat keine reguläre IQ-Datei mit SigMF-Metadaten erzeugt.")
    size = path.stat().st_size
    if not 0 < size <= MAX_EXPORT_BYTES or size % 8:
        raise OnAirError("ON-AIR-Ausgabe hat eine ungültige Größe oder überschreitet 128 MiB.")
    if metadata_path.stat().st_size > 65536:
        raise OnAirError("ON-AIR-Metadaten sind zu groß.")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        glob = metadata["global"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise OnAirError("ON-AIR-SigMF-Metadaten sind ungültig.") from exc
    if not isinstance(glob, dict) or glob.get("core:datatype") != "cf32_le" or glob.get("core:dataset") != path.name:
        raise OnAirError("ON-AIR-SigMF-Datentyp oder Dateiverweis ist unerwartet.")
    rate = _number(glob.get("core:sample_rate"), "native sample rate", 1, 1e9)
    expected_rate = entry.get("sample_rate")
    if expected_rate is not None and not math.isclose(rate, expected_rate, rel_tol=1e-9, abs_tol=.01):
        raise OnAirError("ON-AIR hat eine andere native Rate als das gepinnte Generatorregister verwendet.")
    if glob.get("core:description") != entry["expected_description"]:
        raise OnAirError("ON-AIR meldet eine unerwartete Modusfamilie; kein gültiger Export für diese Auswahl.")
    full_count = size // 8
    if type(glob.get("core:sample_count")) is not int or glob["core:sample_count"] != full_count:
        raise OnAirError("ON-AIR-Samplezahl stimmt nicht mit der IQ-Datei überein.")
    count = requested_count if requested_count is not None else full_count
    if count > MAX_RETURN_SAMPLES:
        raise OnAirError("Vollständiger ON-AIR-Export ist zu lang; einen expliziten sample_count-Ausschnitt wählen.")
    if count > full_count:
        raise OnAirError("ON-AIR-Export ist kürzer als der ausdrücklich angeforderte Ausschnitt.")
    iq = np.empty(count, dtype=np.complex64)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for begin in range(0, count, 262_144):
            chunk_count = min(262_144, count - begin)
            raw = stream.read(chunk_count * 8)
            if len(raw) != chunk_count * 8:
                raise OnAirError("ON-AIR-Datei wurde während des Lesens verkürzt.")
            digest.update(raw)
            iq[begin:begin + chunk_count] = np.frombuffer(raw, dtype="<c8")
    if not np.all(np.isfinite(iq)) or not np.any(np.abs(iq) > 0):
        raise OnAirError("ON-AIR hat nicht endliche Werte oder ausschließlich Stille erzeugt.")
    return iq, rate, full_count, digest.hexdigest()


def generate(spec, *, workdir, stop_event=None, timeout=30):
    """Generate a native-rate fixture, optionally an explicitly selected prefix.

    ``sample_count`` is deliberate segment selection, never a promise that a
    complete frame, payload, or transmission cycle fits inside that segment.
    The destination and executable cannot be supplied through ``spec``.
    """
    entry, params, count, seed = _prepare(spec)
    timeout = _number(timeout, "timeout", .1, 120)
    if stop_event is not None and stop_event.is_set():
        raise OnAirError("ON-AIR-Export abgebrochen.")
    binary = _binary()
    if binary is None:
        raise OnAirError("ON-AIR CLI fehlt. SIGNALCLASSIFIER_ONAIR_CLI auf eine separat installierte CLI setzen.")
    root = Path(workdir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="onair-export-", dir=root) as folder:
        path = Path(folder) / "fixture.cf32"
        argv = [binary, "--synthetic", "--standard", entry["native_cli_id"],
                "--record", str(path), "--secs", str(params["export_seconds"]),
                "--snr", str(params["snr_db"]), "--freq", "0", "--compute", "cpu"]
        # The native CLI does not set these generator selectors merely from
        # --standard. Without them it would export DVB-T2 under another label.
        if entry["native_cli_id"] == "dvbt":
            argv.append("--dvbt")
        elif entry["native_cli_id"] == "atsc":
            argv.append("--atsc")
        for key in sorted(params):
            if key.startswith("sopt"):
                argv.extend(("--sopt", key[4:], str(params[key])))
            elif key.startswith("sval"):
                argv.extend(("--sval", key[4:], str(params[key])))
        logs, elapsed = _export(argv, path, stop_event, timeout)
        iq, rate, full_count, waveform_hash = _read_export(path, entry, count)
    facets = copy.deepcopy(entry["facets"])
    if entry["native_cli_id"] in ("general", "am"):
        modulation = {0: "NFM", 1: "WFM", 2: "AM", 3: "USB", 4: "LSB", 5: "CW"}.get(params.get("sopt0", 2 if entry["native_cli_id"] == "am" else 0))
        facets["modulation"] = modulation
        if params.get("sopt1") == 1:
            facets.update(modulation=None, protocol=None)
    return {"iq": iq, "sample_rate": rate, "facets": facets, "parameters": params,
            "estimated_bandwidth_hz": entry.get("estimated_bandwidth_hz"),
            "provenance": {"engine": "onair_external_cli", "mode_id": entry["id"],
                "source_release": SOURCE_RELEASE, "source_commit": SOURCE_COMMIT,
                "source_url": entry["source_url"], "executable_version_verified": False,
                "implementation_kind": "external_fixture", "native_quantization_bits": 8,
                "native_sample_rate": rate, "requested_seed": seed, "native_seed_control": False,
                "payload_template_id": entry["id"], "payload_family_root": entry["id"],
                "source_waveform_sha256": waveform_hash, "truncated": len(iq) < full_count,
                "full_export_sample_count": full_count, "returned_sample_count": len(iq),
                "scope": "fixture_segment_no_protocol_compliance_proof",
                "sample_count_selection": "explicit_prefix" if count is not None else "full_export",
                "native_metadata_verification": "family_description_and_rate_only_not_submode_or_options",
                "native_metadata_center_frequency": "synthetic_zero_or_absent_not_an_RF_measurement",
                "export_wall_seconds": elapsed, "log_tail": logs[-MAX_LOG_BYTES:],
                "limitations": copy.deepcopy(entry.get("limitations", []))}}
