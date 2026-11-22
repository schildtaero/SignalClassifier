#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import signalclassifier,fastapi,uvicorn,websockets,multipart,numpy,scipy,psutil' >/dev/null 2>&1; then
  ./setup.sh
fi
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
exec .venv/bin/python -m signalclassifier "$@"
