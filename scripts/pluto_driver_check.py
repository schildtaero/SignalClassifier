#!/usr/bin/env python3
"""Check Pluto libraries, optionally finding hardware without enabling TX."""
from __future__ import annotations

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="USB-Geräte suchen oder angegebene URI öffnen")
    parser.add_argument("--uri", default="", help="Explizites Gerät, beispielsweise ip:192.168.2.1")
    args = parser.parse_args()
    try:
        import iio
        import adi
        version = iio.version
        if not version or int(version[0]) != 0:
            raise ValueError("Dieser Installationspfad benötigt die stabile libiio-0.x-API")
        result = {"libiio_version": list(version), "pyadi_version": getattr(adi, "__version__", "unknown"),
                  "python_iio": iio.__file__, "python_adi": adi.__file__}
        if args.probe:
            contexts = {args.uri: "Explizite URI"} if args.uri else iio.scan_contexts("usb")
            found = []
            errors = []
            for uri, description in contexts.items():
                try:
                    context = iio.Context(uri)
                    if "ad9361-phy" in [device.name for device in context.devices]:
                        found.append({"uri": uri, "description": description})
                    del context
                except (OSError, RuntimeError) as exc:
                    errors.append({"uri": uri, "error": str(exc)})
            result.update(pluto_contexts=found, probe_errors=errors)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if found else 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as exc:
        parser.exit(1, f"Pluto-Prüfung: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
