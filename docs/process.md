# The automated architecture governance process

ArchFlow models the typical enterprise-architecture change process as a
pipeline of stages with **gates**. A request only leaves a stage when its
gate is satisfied; entering a stage triggers **automation actions** that do
the repetitive work (document generation, model generation, publication).

```
intake → triage → stakeholder_analysis → drafting → peer_review → board_approval → publication → done
                                                                      ▲
                                             small requests skip ─────┘   (fast track)
any stage can exit to: rejected
```

## Stages, gates and automation

| Stage | Gate (to leave) | Automation (on enter) |
|---|---|---|
| `intake` | title, description and requester filled | — |
| `triage` | classification set (small/medium/large) + ≥1 impacted domain | — |
| `stakeholder_analysis` | ≥1 stakeholder with a concern + stakeholder map generated | **generates the ArchiMate stakeholder map** (`artifacts/<id>/stakeholder_map.archimate.xml`) |
| `drafting` | PSA document generated + ≥1 recorded decision | **generates the PSA document** (`artifacts/<id>/psa.md`) |
| `peer_review` | ≥1 `approve` review, no unresolved `request_changes` | — |
| `board_approval` | ≥1 decision with status `approved` | — |
| `publication` | publication artifact exists | **publishes to BiZZdesign Horizzon** (or exports an Open Exchange file when Horizzon is not configured) |

Checklist items that can be verified objectively are **auto-completed** by the
engine (e.g. `triage.classified` completes itself the moment a classification
is set). Human judgement items are completed with `archflow complete`.

Every transition, review, decision and artifact is recorded in an append-only
**event log** — the audit trail your governance board wants to see.

## Why gates instead of free-form status?

The lengthy manual process this replaces mostly consists of *waiting to
discover what is missing*. Gates make the missing work explicit: `archflow
advance` either moves the request forward or tells you exactly which
conditions block it. Nothing advances "by accident", and nothing waits for a
weekly meeting to learn its next step.

## Where AI agents plug in

Each stage's work can be delegated to a Claude Code agent defined in
`.claude/agents/`:

- `stakeholder-analyst` fills the stakeholder analysis from raw request text.
- `architecture-reviewer` produces the peer review and records a verdict.
- `archimate-modeler` refines generated views or builds additional ones.

The `/intake-request` and `/run-governance` skills orchestrate these agents
around the CLI so a whole request can be driven conversationally.
