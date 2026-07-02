#!/usr/bin/env bash
# One-shot verification: lint, type-check, test. Used by humans, CI and AI agents alike.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "error: .venv not found — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

echo "==> ruff"
"$PY" -m ruff check src tests
echo "==> mypy"
"$PY" -m mypy
echo "==> pytest"
"$PY" -m pytest -q
echo "==> all checks passed"
