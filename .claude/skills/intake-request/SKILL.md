---
name: intake-request
description: >
  Register a new architecture request in ArchFlow from raw material (email,
  meeting notes, issue text, or a short verbal description). Use when the user
  says something like "new architecture request", "intake this", or pastes a
  request they want in the governance process.
---

# Intake an architecture request

Turn raw request material into a registered ArchFlow request.

1. Extract from the material (ask only if truly absent): a crisp **title**,
   **description**, **requester**, **business goal**, and **impacted domains**.
2. Delegate stakeholder discovery to the `stakeholder-analyst` agent with the
   raw material; take its stakeholders/concerns output.
3. Register the request:

   ```bash
   .venv/bin/archflow new \
     --title "..." --description "..." --requester "..." \
     --business-goal "..." \
     --domain "Domain1" --domain "Domain2" \
     --stakeholder "Name:Role:concern one|concern two" \
     --stakeholder "Other Name:Role:concern"
   ```

4. Show the created request (`archflow show <id>`) and report: the request id,
   current stage, and the open checklist items that block the next stage.

Never invent requester names or stakeholders — everything must trace back to
the source material or be flagged as an assumption.
