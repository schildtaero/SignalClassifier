from __future__ import annotations

import argparse
import logging
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="SignalClassifier · lokales SDR-Labor")
    parser.add_argument("--host", default="127.0.0.1", help="Bind-Adresse (Standard: nur dieser Rechner)")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    # Preview runners append Vite's flag; uvicorn already fails on a busy port.
    parser.add_argument("--strictPort", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
