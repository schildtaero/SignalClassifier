#!/usr/bin/env python3
"""Check the installed vendor API without requiring an attached receiver."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import re
from pathlib import Path


def parse_version(header: str) -> tuple[int, int]:
    match = re.search(r"^\s*#\s*define\s+SDRPLAY_API_VERSION\b[^\n]*?\b(\d+)\.(\d+)\b", header, re.MULTILINE)
    if not match:
        raise ValueError("SDRPLAY_API_VERSION fehlt im installierten Header")
    return int(match[1]), int(match[2])


def inspect_api():
    paths = (Path("/usr/local/include/sdrplay_api.h"), Path("/usr/include/sdrplay_api.h"))
    header = next((path for path in paths if path.is_file()), None)
    if header is None:
        raise ValueError("SDRplay-API-Header fehlt")
    version = parse_version(header.read_text(encoding="utf-8"))
    if version[0] != 3 or version < (3, 15):
        raise ValueError(f"SDRplay API v3.15+ benötigt; Header meldet {version[0]}.{version[1]}")
    name = ctypes.util.find_library("sdrplay_api")
    if not name:
        raise ValueError("libsdrplay_api fehlt im Suchpfad; gegebenenfalls sudo ldconfig ausführen")
    library = ctypes.CDLL(name)
    for symbol in ("sdrplay_api_Open", "sdrplay_api_Close", "sdrplay_api_ApiVersion", "sdrplay_api_GetDevices", "sdrplay_api_Init"):
        if not hasattr(library, symbol):
            raise ValueError(f"SDRplay-Bibliothek ist inkompatibel: {symbol} fehlt")
    return {"header": str(header), "library": name, "api_header_version": f"{version[0]}.{version[1]}"}, library


def runtime_version(library) -> str:
    # The vendor specification requires Open before every other API operation.
    library.sdrplay_api_Open.argtypes = []
    library.sdrplay_api_Open.restype = ctypes.c_int
    library.sdrplay_api_Close.argtypes = []
    library.sdrplay_api_Close.restype = ctypes.c_int
    library.sdrplay_api_ApiVersion.argtypes = [ctypes.POINTER(ctypes.c_float)]
    library.sdrplay_api_ApiVersion.restype = ctypes.c_int
    result = library.sdrplay_api_Open()
    if result:
        raise ValueError(f"SDRplay API Open fehlgeschlagen (Code {result}); Dienst sdrplay prüfen")
    try:
        value = ctypes.c_float()
        result = library.sdrplay_api_ApiVersion(ctypes.byref(value))
        if result:
            raise ValueError(f"SDRplay ApiVersion fehlgeschlagen (Code {result}); Bibliothek und Dienst prüfen")
        text = f"{value.value:.2f}"
        parts = tuple(map(int, text.split(".")))
        if parts[0] != 3 or parts < (3, 15):
            raise ValueError(f"Installierte Laufzeit-API {text} ist nicht unterstützt")
        return text
    finally:
        library.sdrplay_api_Close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true", help="Zusätzlich Open → ApiVersion → Close mit dem Dienst testen")
    args = parser.parse_args()
    try:
        result, library = inspect_api()
        if args.runtime:
            result["api_runtime_version"] = runtime_version(library)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"SDRplay-Prüfung: {exc}\n")


if __name__ == "__main__":
    main()
