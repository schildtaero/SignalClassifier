#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sc_onair_python="${SIGNALCLASSIFIER_PYTHON:-python3}"
exec "$sc_onair_python" scripts/install_onair.py "$@"
