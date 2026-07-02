---
name: run-governance
description: >
  Drive an ArchFlow architecture request forward through the governance
  pipeline (triage → stakeholder analysis → drafting → review → approval →
  publication), doing or delegating the work each gate requires. Use when the
  user says "advance request X", "push this through the process", or asks why
  a request is stuck.
---

# Run the governance process for a request

Work stage by stage; never skip a gate.

1. `archflow show <id>` — read stage, open checklist items and history.
2. `archflow advance <id>` — if it advances, artifacts are generated
   automatically (stakeholder map on entering stakeholder_analysis, PSA
   document on entering drafting, Horizzon publication on entering
   publication). If it's **blocked**, the reasons tell you what work remains:
   - *triage*: agree classification (small/medium/large) and impacted domains
     with the user, then `archflow complete <id> triage.classified` after
     setting them.
   - *stakeholder analysis*: use the `stakeholder-analyst` agent, add missing
     stakeholders, then advance again.
   - *drafting*: review the generated PSA under `artifacts/<id>/psa.md`,
     improve it, record key decisions with `archflow decide <id> --title ...`.
   - *peer review*: run the `architecture-reviewer` agent on the PSA and the
     stakeholder map, then record the verdict with `archflow review <id> ...`.
     A request needs at least one `approve` to pass.
   - *board approval*: this is a human gate — prepare a one-page summary of
     the request and its decisions for the user to take to the board; record
     the outcome with `archflow decide <id> --status approved --decided-by ...`.
   - *publication*: check Horizzon settings in `.env`; without them ArchFlow
     exports an ArchiMate Open Exchange file under `artifacts/<id>/` for
     manual import into BiZZdesign Enterprise Studio.
3. Repeat advance → fix-blockers until the request reaches `done`, the user
   stops you, or a gate needs a human decision (board approval always does).
4. Finish with a status report: stage reached, artifacts produced (paths),
   and what remains.

Boundaries: never fabricate review verdicts or board decisions — reviews come
from the architecture-reviewer agent's genuine findings; board approval only
from the user.
