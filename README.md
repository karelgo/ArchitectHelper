# ArchFlow — Architecture Process Automation

ArchFlow automates a lengthy enterprise-architecture governance process from
end to end:

```
intake → triage → stakeholder analysis → drafting → peer review → board approval → publication
```

Each stage has an explicit **gate** (what must be true to move on) and
**automation** (what gets generated on entry): ArchiMate stakeholder maps,
Project Start Architecture documents, and publication to **BiZZdesign
Horizzon** — with a standards-based ArchiMate Open Exchange file export as
the always-available fallback. Every step lands in an append-only audit
trail.

The repo is also **AI-agent ready**: `CLAUDE.md`, custom subagents and
skills, a session-start hook, CI and a `@claude` GitHub workflow are all set
up, so Claude Code (or any coding agent) can pick up work immediately —
including doing the actual architecture work (stakeholder analysis, peer
review) through the process.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
scripts/verify.sh                       # ruff + mypy + pytest
```

Walk a request through the whole process:

```bash
alias archflow=.venv/bin/archflow

archflow new --title "CRM renewal" \
  --description "Replace the aging CRM platform" \
  --requester "Alice" --business-goal "Happier customers" \
  --stakeholder "Alice:Sales director:Adoption risk|Budget" \
  --stakeholder "Bob:CISO:Data protection"

archflow advance <id>       # intake → triage
archflow triage <id> --classification medium --domain CRM --domain Integration
archflow advance <id>       # → stakeholder analysis: generates the ArchiMate map
archflow advance <id>       # → drafting: generates the PSA document
archflow decide <id> --title "Buy over build" --rationale "Speed"
archflow advance <id>       # → peer review
archflow review <id> --reviewer "Bob" --verdict approve
archflow advance <id>       # → board approval
archflow decide <id> --title "Board approval" --status approved --decided-by "Board"
archflow advance <id>       # → publication: pushes to Horizzon / exports exchange file
archflow advance <id>       # → done
archflow events <id>        # the full audit trail
```

If a gate isn't satisfied, `advance` tells you exactly what's missing —
that's the point: no more waiting for a weekly meeting to find out.

Prefer HTTP? `archflow serve` starts a REST API with interactive docs at
`http://127.0.0.1:8000/docs`.

## Web UI: governance board + Studio

`archflow serve` also hosts a web app at `http://127.0.0.1:8000/ui/`:

- **Governance** — the pipeline as a kanban with a gate-aware request
  drawer: advance stages, see exactly what blocks a gate, record triage,
  stakeholders, reviews and decisions, browse artifacts and the audit
  timeline. With an API key set, stage-matched **AI assist** buttons draft
  the stakeholder analysis, the PSA prose and a pre-review — always as
  proposals a human filters and applies, never auto-recorded.
- **Studio** — an ArchiMate view designer with the **draw.io editor
  embedded** (diagrams render with proper layer colours and relationship
  notations; your layout edits sync back), an **ArchiMate linter** (a
  problems panel flags illegal relationships, dangling references and more)
  and an **AI copilot** that builds views from A to Z: describe the system,
  it models elements per layer, wires semantically correct relationships,
  lints its own work and lays out the view live on the canvas. Set
  `ARCHFLOW_ANTHROPIC_API_KEY` to enable the copilot.

Details: [docs/studio.md](docs/studio.md).

## BiZZdesign Horizzon

Configure your tenant in `.env` (see `.env.example`) and publication pushes
generated elements/relations into Horizzon via the Bizzdesign Open API
(OAuth2 client credentials). Because the Open API has no view endpoints,
the stakeholder-map *view* is delivered as an ArchiMate Open Exchange file
for one-click import into Enterprise Studio. Unconfigured or unreachable?
Publication degrades gracefully to the file export. Details, setup steps and
gotchas: [docs/horizzon-integration.md](docs/horizzon-integration.md).

## Working with AI agents

- `CLAUDE.md` / `AGENTS.md` — commands, architecture map, invariants.
- `.claude/agents/` — `stakeholder-analyst`, `architecture-reviewer`,
  `archimate-modeler`: specialists for the stages that need judgement.
- Skills — `/intake-request` (register a request from raw text) and
  `/run-governance` (drive a request through the gates).
- `.claude/hooks/session-start.sh` — cloud sessions install deps automatically.
- `.github/workflows/claude.yml` — mention `@claude` in issues/PRs to
  delegate work; `claude-review.yml` gives every PR a first-pass review
  (add the `ANTHROPIC_API_KEY` repo secret to enable both).

## Repository map

| Path | What lives there |
|---|---|
| `src/archflow/domain/` | Domain models + audit events (everything depends on this) |
| `src/archflow/workflow/` | Engine, stage gates/checklists, automation actions, document rendering |
| `src/archflow/archimate/` | ArchiMate model, Open Exchange XML I/O, stakeholder-map generator |
| `src/archflow/horizzon/` | Bizzdesign Open API client + publisher (file-export fallback) |
| `src/archflow/api/`, `src/archflow/cli.py` | FastAPI app and Typer CLI |
| `docs/` | [Process](docs/process.md) · [Architecture](docs/architecture.md) · [Horizzon](docs/horizzon-integration.md) |

## Development

```bash
scripts/verify.sh                                  # the whole gauntlet
.venv/bin/python -m pytest tests/test_api.py -q    # one file
.venv/bin/ruff check src tests --fix               # lint
```

Conventions and invariants that matter are in [CLAUDE.md](CLAUDE.md) — they
apply to humans too.
