# AGENTS.md

Canonical agent guidance lives in [CLAUDE.md](CLAUDE.md) — read that first.
It covers build/test commands, the package map, and the invariants that will
bite you (ArchiMate exchange-format rules, stage-transition rules, config).

Quick start for any coding agent:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
scripts/verify.sh   # ruff + mypy + pytest — must pass before you're done
```
