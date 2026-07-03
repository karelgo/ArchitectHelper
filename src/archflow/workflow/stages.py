"""Stage checklists and gate rules for the governance pipeline."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from archflow.domain.models import (
    ArchitectureRequest,
    ArtifactKind,
    ChecklistItem,
    DecisionStatus,
    ReviewVerdict,
    Stage,
    utcnow,
)

#: Seeded checklist definitions per stage: (key, description).
_CHECKLIST_SEED: dict[Stage, list[tuple[str, str]]] = {
    Stage.INTAKE: [
        ("intake.described", "Title, description and requester are filled in"),
    ],
    Stage.TRIAGE: [
        ("triage.classified", "Request classification is set (small / medium / large)"),
        ("triage.domains", "At least one impacted domain is recorded"),
    ],
    Stage.STAKEHOLDER_ANALYSIS: [
        ("sa.stakeholders", "At least one stakeholder with at least one concern is recorded"),
        ("sa.map", "Stakeholder map artifact has been generated"),
    ],
    Stage.DRAFTING: [
        ("draft.psa", "Project Start Architecture document has been generated"),
        ("draft.decisions", "At least one architecture decision is recorded"),
    ],
    Stage.PUBLICATION: [
        ("pub.published", "Results are published (ArchiMate export produced)"),
    ],
}

#: Objective completion conditions for auto-completable checklist items.
_AUTO_CONDITIONS: dict[str, Callable[[ArchitectureRequest], bool]] = {
    "intake.described": lambda r: bool(r.title and r.description and r.requester),
    "triage.classified": lambda r: r.classification is not None,
    "triage.domains": lambda r: len(r.impacted_domains) >= 1,
    "sa.stakeholders": lambda r: any(s.concerns for s in r.stakeholders),
    "sa.map": lambda r: r.artifact_of_kind(ArtifactKind.STAKEHOLDER_MAP) is not None,
    "draft.psa": lambda r: r.artifact_of_kind(ArtifactKind.PSA_DOCUMENT) is not None,
    "draft.decisions": lambda r: len(r.decisions) >= 1,
    "pub.published": lambda r: r.artifact_of_kind(ArtifactKind.ARCHIMATE_EXPORT) is not None,
}

#: Checklist keys whose completion is determined objectively by
#: :func:`auto_complete` — they cannot be completed by hand.
AUTO_KEYS: frozenset[str] = frozenset(_AUTO_CONDITIONS)


def checklist_for(stage: Stage) -> list[ChecklistItem]:
    """Seeded checklist items for one stage (empty for gate-only stages)."""
    return [
        ChecklistItem(key=key, description=description, stage=stage)
        for key, description in _CHECKLIST_SEED.get(stage, [])
    ]


def auto_complete(request: ArchitectureRequest) -> list[str]:
    """Mark checklist items done when their condition is objectively met.

    Returns the keys of items newly completed by this call.
    """
    completed: list[str] = []
    for item in request.checklist:
        if item.done:
            continue
        condition = _AUTO_CONDITIONS.get(item.key)
        if condition is not None and condition(request):
            item.done = True
            item.completed_by = "auto"
            item.completed_at = utcnow()
            completed.append(item.key)
    return completed


def _peer_review_reasons(request: ArchitectureRequest) -> list[str]:
    reviews = [r for r in request.reviews if r.stage == Stage.PEER_REVIEW]
    approvals = [r for r in reviews if r.verdict == ReviewVerdict.APPROVE]
    if not approvals:
        return ["Peer review requires at least one approving review"]
    latest_approval = max(approvals, key=lambda r: r.created_at)
    changes_after = [
        r
        for r in reviews
        if r.verdict == ReviewVerdict.REQUEST_CHANGES and r.created_at > latest_approval.created_at
    ]
    if changes_after:
        return ["Changes were requested after the latest approving review"]
    return []


class GateCondition(BaseModel):
    """One thing the current stage's gate checks, with its live state."""

    key: str
    description: str
    met: bool


def gate_report(request: ArchitectureRequest) -> list[GateCondition]:
    """Every condition the current gate checks, met or not.

    The UI shows this *before* an advance is attempted, so users see what
    the gate needs instead of discovering it by trial and error. Must agree
    with :func:`gate_for` — the checklist part is derived from the same
    items, the extra branches mirror its rules.
    """
    conditions = [
        GateCondition(key=item.key, description=item.description, met=item.done)
        for item in request.checklist
        if item.stage == request.stage
    ]
    if request.stage == Stage.PEER_REVIEW:
        conditions.append(
            GateCondition(
                key="review.approved",
                description="An approving peer review, with no changes requested after it",
                met=not _peer_review_reasons(request),
            )
        )
    elif request.stage == Stage.BOARD_APPROVAL:
        conditions.append(
            GateCondition(
                key="board.approved",
                description="A decision approved at the board-approval stage",
                met=any(
                    d.status == DecisionStatus.APPROVED and d.stage == Stage.BOARD_APPROVAL
                    for d in request.decisions
                ),
            )
        )
    return conditions


def gate_for(request: ArchitectureRequest) -> tuple[bool, list[str]]:
    """Can the request leave its current stage?

    Returns ``(ok, reasons)`` where ``reasons`` lists human-readable blockers.
    """
    reasons: list[str] = []
    # Triage conditions are fully covered by the checklist auto-conditions;
    # only stages with rules the checklist cannot express get extra branches.
    for item in request.open_checklist(request.stage):
        reasons.append(f"Checklist item '{item.key}' not done: {item.description}")

    # Defense in depth: a request whose checklist is missing this stage's
    # seeded items (constructed outside create_request, partial data) must
    # not sail through on an accidentally empty list.
    seeded_keys = {key for key, _ in _CHECKLIST_SEED.get(request.stage, [])}
    present_keys = {i.key for i in request.checklist if i.stage == request.stage}
    for missing in sorted(seeded_keys - present_keys):
        reasons.append(f"Checklist item '{missing}' is missing from this request")

    if request.stage == Stage.PEER_REVIEW:
        reasons.extend(_peer_review_reasons(request))
    elif request.stage == Stage.BOARD_APPROVAL and not any(
        d.status == DecisionStatus.APPROVED and d.stage == Stage.BOARD_APPROVAL
        for d in request.decisions
    ):
        reasons.append(
            "Board approval requires a decision approved at the board-approval stage"
        )

    return (not reasons, reasons)
