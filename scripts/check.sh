#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "$0")/.."

VENV_BIN=".conomi/bin"

echo "== ruff =="
"$VENV_BIN/ruff" check conomi tests

echo "== mypy --strict =="
"$VENV_BIN/mypy" conomi

echo "== float guard (no float literals anywhere a length or quantity is handled) =="

if grep -rnP '(^|[^A-Za-z0-9_.])float\(|[0-9]+\.[0-9]+(?![0-9]*["'"'"'])' conomi/ \
    --include='*.py' | grep -v '# allow-float'; then
    echo "float literal or float() call found in conomi/ -- see CLAUDE.md §4/§6" >&2
    exit 1
fi

echo "== pytest =="
"$VENV_BIN/pytest" "$@"

echo "== all green =="
