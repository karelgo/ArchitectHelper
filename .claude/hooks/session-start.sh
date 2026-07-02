#!/bin/bash
# SessionStart hook: make sure the venv exists and dev deps are installed so
# ruff / mypy / pytest work immediately in Claude Code on the web sessions.
set -euo pipefail

# Local sessions manage their own venv; only run in remote (web) sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet -e ".[dev]"
echo "archflow dev environment ready" >&2
