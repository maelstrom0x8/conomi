#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")/.."

VENV_BIN=".conomi/bin"

echo "== ruff =="
"$VENV_BIN/ruff" check conomi tests

echo "== mypy --strict =="
"$VENV_BIN/mypy" conomi

echo "== float guard (no float literals anywhere a length or quantity is handled) =="
"$VENV_BIN/python" scripts/float_guard.py

echo "== pytest =="
"$VENV_BIN/pytest" "$@"

echo "== all green =="
