"""Stage checklists and gate rules for the governance pipeline."""

from __future__ import annotations

from collections.abc import Callable

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


def gate_for(request: ArchitectureRequest) -> tuple[bool, list[str]]:
    """Can the request leave its current stage?

    Returns ``(ok, reasons)`` where ``reasons`` lists human-readable blockers.
    """
    reasons: list[str] = []
    # Triage conditions are fully covered by the checklist auto-conditions;
    # only stages with rules the checklist cannot express get extra branches.
    for item in request.open_checklist(request.stage):
        reasons.append(f"Checklist item '{item.key}' not done: {item.description}")

    if request.stage == Stage.PEER_REVIEW:
        reasons.extend(_peer_review_reasons(request))
    elif request.stage == Stage.BOARD_APPROVAL and not any(
        d.status == DecisionStatus.APPROVED for d in request.decisions
    ):
        reasons.append("Board approval requires at least one approved decision")

    return (not reasons, reasons)
