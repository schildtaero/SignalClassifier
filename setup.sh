#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode=core
for arg in "$@"; do
  case "$arg" in
    --neural-cpu) mode=cpu ;;
    --neural) mode=neural ;;
    --pluto) mode=pluto ;;
    --check) mode=check ;;
    --help) echo 'Aufruf: ./setup.sh [--check | --neural-cpu | --neural | --pluto]'; exit 0 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done
python_cmd="${SIGNALCLASSIFIER_PYTHON:-python3}"
if [[ "$mode" == check ]]; then
  if [[ -x .venv/bin/python ]]; then python_cmd=.venv/bin/python; fi
  "$python_cmd" -c 'import sys; import numpy, scipy, fastapi, uvicorn, multipart; print("Python", sys.version.split()[0], "· Kernabhängigkeiten vorhanden")'
  exit
fi
if ! "$python_cmd" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
  echo 'Python 3.11+ benötigt (Ubuntu 24.04 / Debian 12+). SIGNALCLASSIFIER_PYTHON kann den Interpreter wählen.' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  if ! "$python_cmd" -m venv .venv; then
    echo 'Python venv fehlt. Unter Ubuntu/Debian: sudo apt install python3-venv' >&2
    exit 1
  fi
fi
.venv/bin/python -m pip install -e .
case "$mode" in
  cpu) .venv/bin/python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu ;;
  neural) .venv/bin/python -m pip install 'torch>=2.6,<3' ;;
  pluto) .venv/bin/python -m pip install -e '.[pluto]' ;;
esac
echo 'Bereit. Start: ./start.sh · http://127.0.0.1:8090'
if [[ "$mode" == pluto ]]; then
  echo 'Pluto benötigt zusätzlich das native libiio (Ubuntu/Debian: sudo apt install libiio0). URI z.B. ip:192.168.2.1.'
fi
