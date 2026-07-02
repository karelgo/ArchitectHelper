# ArchFlow architecture

Layered, dependency-inverted: everything depends on `domain`, nothing in
`domain` depends on anything else.

```
                    ┌─────────────┐  ┌──────────┐
   interfaces       │  api (REST) │  │   cli    │
                    └──────┬──────┘  └────┬─────┘
                           └───────┬──────┘
                    ┌──────────────▼──────────────┐
   process          │   workflow (engine, gates,  │
                    │   actions, documents)       │
                    └──┬─────────┬────────────┬───┘
                       │         │            │
              ┌────────▼──┐ ┌────▼──────┐ ┌───▼──────────┐
   supporting │  storage  │ │ archimate │ │   horizzon   │
              │ (SQLite)  │ │ (model+   │ │ (API client +│
              │           │ │  OEF XML) │ │  publisher)  │
              └────────┬──┘ └────┬──────┘ └───┬──────────┘
                       └─────────▼────────────┘
                    ┌─────────────────────────────┐
   core             │  domain (models, events)    │
                    └─────────────────────────────┘
```

## Packages

- **`archflow.domain`** — pydantic models: `ArchitectureRequest` (aggregate
  root), `Stage` pipeline + `next_stage`, stakeholders/drivers/goals/
  assessments, artifacts, checklist, reviews, decisions; audit `Event`s.
- **`archflow.workflow`** — `WorkflowEngine` moves requests through stages.
  `stages.py` defines per-stage checklists and gate rules; `actions.py` is a
  registry of on-enter automation (map generation, PSA rendering,
  publication) with lazy imports so modules stay independently testable;
  `documents.py` renders Jinja2 templates from `archflow/templates/`.
- **`archflow.archimate`** — in-memory ArchiMate model (`model.py`),
  ArchiMate 3.x Open Exchange XML reader/writer (`openexchange.py`, stdlib
  ElementTree, namespace `http://www.opengroup.org/xsd/archimate/3.0/`), and
  the stakeholder-map generator (`stakeholder_map.py`) that turns a request's
  stakeholders/concerns/drivers/goals into a laid-out motivation view.
- **`archflow.horizzon`** — OAuth2 client-credentials auth, HTTP client for
  the BiZZdesign Horizzon API, and a `HorizzonPublisher` port: publishes via
  the API when configured, otherwise falls back to exporting an Open Exchange
  file that Enterprise Studio imports. See `docs/horizzon-integration.md`.
- **`archflow.storage`** — SQLAlchemy-on-SQLite persistence (requests as
  JSON documents + append-only event log). Swap `ARCHFLOW_DATABASE_URL` for
  Postgres in production.
- **`archflow.api` / `archflow.cli`** — FastAPI app (`create_app` factory,
  injectable engine for tests) and Typer CLI (`archflow` entry point).

## Key design decisions

1. **Gates over statuses** — a request can't be "moved" without satisfying
   explicit, testable conditions; blocked advances return the reasons.
2. **Automation as pluggable actions** — on-enter actions live in a registry;
   tests inject stubs, and organisations can add their own actions (e.g. post
   to Teams) without touching the engine.
3. **Open Exchange as the interchange backbone** — generated models are
   standard ArchiMate 3.x exchange files, so they import into BiZZdesign,
   Archi, or any certified tool even with no API connectivity at all.
4. **Requests stored as documents** — the aggregate is serialized whole
   (JSON in SQLite); no ORM mapping to drift out of sync with the domain.
