# Studio: the web UI, draw.io and the ArchiMate copilot

`archflow serve` hosts a web app at `http://127.0.0.1:8000/ui/` with two
workspaces:

## Governance

A kanban of the pipeline (intake → … → publication) with a KPI strip.
Clicking a card opens the request drawer: gate-aware **Advance** (blocked
advances list exactly what's missing), triage/stakeholder/review/decision
forms that appear only in the stages where they're valid, artifacts,
checklist and the full audit timeline. **New request** registers an intake
with stakeholders in `Name : Role : concern | concern` format.

## Studio

A three-pane ArchiMate view designer:

- **Left — model outline**: elements grouped by layer with the standard
  ArchiMate layer colours, plus relationship/view counts.
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
Open Exchange file.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `ARCHFLOW_DRAWIO_EMBED_URL` | `https://embed.diagrams.net` | Point at a self-hosted draw.io to keep diagrams off the public CDN. Offline, the canvas shows a fallback note; everything else keeps working. |
| `ARCHFLOW_ANTHROPIC_API_KEY` | _(empty)_ | Enables the copilot. Without it the panel explains how to enable it; manual drawing, imports and request-seeding still work. |
| `ARCHFLOW_ASSISTANT_MODEL` | `claude-opus-4-8` | Copilot model. |

## How the copilot works

Each chat turn runs a tool-use loop server-side (`POST
/studio/views/{id}/assistant`): Claude calls `get_model`, `add_elements`,
`add_relationships`, `remove_elements`, `create_view` and `rename_model`
against the project's canonical model. Validation failures are fed back as
tool results so it self-corrects; every mutation bumps the model revision so
the diagram re-renders (your manual layout survives via the synced view).
Conversation history persists on the project.

## Notes

- The diagram's cell ids embed model element ids (`el-<id>`), which is how
  draw.io edits map back onto the model. Shapes you add free-hand in draw.io
  are preserved in the stored `.drawio` document but are not part of the
  ArchiMate model (and thus not exported to Open Exchange).
- The Studio API is plain REST under `/studio/*` — see `/docs` for the
  interactive schema.
