# Studio: the web UI, draw.io and the ArchiMate copilot

`archflow serve` hosts a web app at `http://127.0.0.1:8000/ui/` with two
workspaces:

## Governance

A kanban of the pipeline (intake → … → publication) with a KPI strip.
Clicking a card opens the request drawer at a shareable URL
(`#/request/{id}` — the **Copy link** button puts it on the clipboard).
The drawer shows the **live gate**: every condition the current gate
checks with its met/unmet state (`GET /requests/{id}/gate`), so the
Advance button says where it goes ("Advance to Peer review") instead of
failing informatively. Actions update the drawer in place — no flicker,
scroll preserved — and Escape closes it. Stage forms
(triage/stakeholder/review/decision) appear only where they're valid,
plus artifacts, an inline **stakeholder-map preview**
(`/requests/{id}/map.svg`, rendered server-side), the checklist and the
audit timeline. **New request** registers an intake with stakeholders in
`Name : Role : concern | concern` format.

The board stays workable at scale: a filter bar narrows by text,
classification or domain; cards carry a time-in-stage clock (amber past 7
days, red past 14) and the owner's initials (assign via the drawer, the
API's `POST /requests/{id}/owner`, or `archflow assign`); finished and
rejected requests collapse into an **Archive** column that opens a
filterable table at `#/archive`. AI drafts persist on the request
(`GET /requests/{id}/assistant/drafts`) so a paid-for draft survives
closing the dialog — the drawer offers "View last draft" next to each
assist button. A first run greets an empty board with seed-an-example.
The whole UI ships a **dark theme** (topbar toggle, follows the OS
preference, remembered per browser) and a keyboard/screen-reader floor:
dialogs trap and return focus, Escape closes, cards are real links,
every field has an accessible name, and validation errors are announced.

### AI assist in the drawer

With an API key configured, the drawer offers stage-matched draft buttons.
Every output is a *draft a human disposes of* — the assistant never advances
a stage, records a verdict, or mutates the request on its own:

- **Draft stakeholder analysis** (intake → stakeholder analysis): proposes
  stakeholders with influence/interest/attitude plus drivers, goals and
  assessments. You untick what doesn't belong and apply the rest; duplicates
  (by name) are skipped, and the application is audited as
  `analysis_applied`.
- **Draft PSA** (stakeholder analysis → peer review): drafts the Project
  Start Architecture prose, improving on the template-generated document if
  one exists. Preview first; saving records it as the request's PSA artifact
  exactly as previewed (no regeneration).
- **AI pre-review** (peer review / board approval): a critical read of the
  PSA and stakeholder analysis with severity-tagged findings and a suggested
  verdict. Display-only: the real verdict goes through the *Record review*
  form.

The same drafts are available from the CLI (`archflow ai stakeholders|psa|
review`) and REST (`POST /requests/{id}/assistant/*`, 503 without a key).

## Studio

A three-pane ArchiMate view designer:

- **Left — model outline**: elements grouped by layer with the standard
  ArchiMate layer colours, plus relationship/view counts — and a **Problems**
  panel driven by the ArchiMate linter (`GET /studio/views/{id}/lint`):
  semantic checks (illegal relationship endpoints such as Influence into
  non-motivation elements or realizing a Stakeholder, cross-layer
  composition) and structural ones (dangling references, duplicate names,
  orphans), errors first. One click sends the findings to the copilot to fix.
- **Center — draw.io**: the real diagrams.net editor embedded via its
  official postMessage protocol. The canonical ArchiMate model renders as a
  draw.io diagram (layer fills, correct relationship notations); edits
  autosave back, and node geometry syncs into the model so exports keep your
  layout. Export as `.archimate.xml` (Open Exchange — imports into
  BiZZdesign/Archi) or `.drawio`.
- **Right — the copilot**: a Claude-powered ArchiMate modeler that goes from
  a description to a finished view: it asks scoping questions, adds elements
  with correct types per layer, wires semantically valid relationships,
  and (re)builds the view — the canvas refreshes live after each change.

Projects can start blank, be seeded from a governance request's stakeholder
map ("Open map in Studio" in the drawer), or be imported from any ArchiMate
Open Exchange file. The gallery shows **live SVG thumbnails**
(`/studio/views/{id}/preview.svg`, rendered server-side); the same
renderer provides a read-only preview when the draw.io embed can't load.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `ARCHFLOW_DRAWIO_EMBED_URL` | `https://embed.diagrams.net` | Point at a self-hosted draw.io to keep diagrams off the public CDN. Offline, the canvas shows a fallback note; everything else keeps working. |
| `ARCHFLOW_ANTHROPIC_API_KEY` | _(empty)_ | Enables the copilot. Without it the panel explains how to enable it; manual drawing, imports and request-seeding still work. |
| `ARCHFLOW_ASSISTANT_MODEL` | `claude-opus-4-8` | Copilot model. |

## How the copilot works

Each chat turn runs a tool-use loop server-side. The UI uses the
streaming variant (`POST /studio/views/{id}/assistant/stream`,
server-sent events): each tool round emits its actions live ("+3
elements · created view"), so long turns show progress instead of a
silent spinner, and the Send button becomes **Stop** while a turn runs.
The non-streaming `POST /studio/views/{id}/assistant` returns the same
result in one response. In the loop Claude calls `get_model`, `add_elements`,
`add_relationships`, `remove_elements`, `create_view`, `rename_model` and
`lint_model` against the project's canonical model. Validation failures are
fed back as tool results so it self-corrects, and it lints its own work
before replying; every mutation bumps the model revision so the diagram
re-renders (your manual layout survives via the synced view). Conversation
history persists on the project. When the project was seeded from a
governance request, the request brief (goal, stakeholders, decisions) rides
along as extra system context, so the copilot models with the actual
concerns in view.

## Notes

- The diagram's cell ids embed model element ids (`el-<id>`), which is how
  draw.io edits map back onto the model. Shapes you add free-hand in draw.io
  are preserved in the stored `.drawio` document but are not part of the
  ArchiMate model (and thus not exported to Open Exchange).
- The Studio API is plain REST under `/studio/*` — see `/docs` for the
  interactive schema.
