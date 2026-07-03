# CLAUDE.md

ArchFlow: automation for an enterprise architecture governance process
(intake → triage → stakeholder analysis → drafting → peer review → board
approval → publication) with ArchiMate stakeholder-map generation and
BiZZdesign Horizzon publication. Python 3.11, src layout, package `archflow`.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # one-time setup
scripts/verify.sh                        # ruff + mypy + pytest (run before committing)
.venv/bin/python -m pytest tests/test_workflow_engine.py -q  # one test file
.venv/bin/python -m pytest -k stakeholder -q                 # by keyword
.venv/bin/ruff check src tests --fix     # lint (line length 100, E501 off)
.venv/bin/archflow --help                # the CLI
.venv/bin/archflow serve                 # REST API on :8000 (docs at /docs)
```

## Map

- `src/archflow/domain/` — pydantic domain models + events. **Everything
  depends on this; it depends on nothing.** Start here to understand the app.
- `src/archflow/workflow/` — `engine.py` (WorkflowEngine), `stages.py`
  (checklists + gate rules per stage), `actions.py` (on-enter automation
  registry; uses lazy imports), `documents.py` + `../templates/*.j2` (PSA
  rendering).
- `src/archflow/archimate/` — ArchiMate model, Open Exchange XML I/O,
  stakeholder-map generator.
- `src/archflow/horizzon/` — BiZZdesign Horizzon client + publisher (falls
  back to Open Exchange file export when unconfigured).
- `src/archflow/api/` (FastAPI, `create_app` factory; routers `studio.py` +
  `assistant.py`) and `src/archflow/cli.py` (Typer, incl. `archflow ai …`).
  `docs/` explains the process, architecture, Horizzon setup and the Studio.
- `src/archflow/studio/` — view projects (ViewProject + StudioService);
  `src/archflow/drawio.py` — ArchiMate ⇄ draw.io mxGraph conversion (cell ids
  `el-<id>`/`rel-<id>` are the mapping contract — don't change them);
  `src/archflow/archimate/lint.py` — the ArchiMate linter (semantic +
  structural rules, errors first);
  `src/archflow/assistant/` — `copilot.py` (Anthropic tool-use loop) and
  `governance.py` (one-shot stage drafts: stakeholders/PSA/pre-review —
  drafts only, humans apply; inject a fake `messages_client` in tests,
  never call the network);
  `src/archflow/ui/static/` — no-build vanilla ES-module web app at `/ui`.

## Rules that bite

- ArchiMate exchange XML: namespace is always
  `http://www.opengroup.org/xsd/archimate/3.0/` (even for 3.1/3.2 models);
  `<model>` child order is name, documentation, properties, metadata,
  elements, relationships, organizations, propertyDefinitions, views;
  identifiers are NCNames → prefix `id-`. Junctions are element types, not
  relationship types. Use stdlib `xml.etree`, never lxml.
- Stage transitions only via the engine (`WorkflowEngine.advance` /
  `reject`) — never set `request.stage` directly in application code; gates
  and the event log depend on it. Checklist items with auto-conditions
  cannot be completed by hand (gate-bypass guard).
- `actions.py` imports archimate/horizzon **inside functions** — keep it that
  way so modules stay independently testable.
- Config only via `archflow.config.Settings` (env prefix `ARCHFLOW_`,
  see `.env.example`). Never read `os.environ` elsewhere; never commit `.env`.
- Pydantic v2 idioms (`model_dump`, `model_validate`); full type hints;
  tests use `tmp_path` sqlite URLs + stub `ActionRegistry`, no real network.
  Horizzon HTTP tests use `httpx.MockTransport`.

## Agents & skills in this repo

`.claude/agents/`: `stakeholder-analyst`, `architecture-reviewer`,
`archimate-modeler` — stage-specific specialists. Skills: `/intake-request`
(register a request from raw text), `/run-governance` (drive a request
through the gates). Prefer delegating stage work to these instead of doing it
inline.
