"""Tests for stage checklists, auto-completion and gates."""

from __future__ import annotations

from datetime import UTC, datetime

from archflow.domain.models import (
    PIPELINE,
    ArchitectureRequest,
    Artifact,
    ArtifactKind,
    Classification,
    Decision,
    DecisionStatus,
    Review,
    ReviewVerdict,
    Stage,
    Stakeholder,
)
from archflow.workflow.stages import auto_complete, checklist_for, gate_for


def make_request(**kwargs: object) -> ArchitectureRequest:
    """A request with the full pipeline checklist seeded."""
    request = ArchitectureRequest.model_validate({"title": "Test request", **kwargs})
    for stage in PIPELINE:
        request.checklist.extend(checklist_for(stage))
    return request


def test_checklist_for_seeds_expected_keys() -> None:
    assert [i.key for i in checklist_for(Stage.INTAKE)] == ["intake.described"]
    assert [i.key for i in checklist_for(Stage.TRIAGE)] == ["triage.classified", "triage.domains"]
    assert [i.key for i in checklist_for(Stage.STAKEHOLDER_ANALYSIS)] == [
        "sa.stakeholders",
        "sa.map",
    ]
    assert [i.key for i in checklist_for(Stage.DRAFTING)] == ["draft.psa", "draft.decisions"]
    assert [i.key for i in checklist_for(Stage.PUBLICATION)] == ["pub.published"]
    assert checklist_for(Stage.PEER_REVIEW) == []
    assert checklist_for(Stage.BOARD_APPROVAL) == []


def test_checklist_items_carry_their_stage() -> None:
    for item in checklist_for(Stage.TRIAGE):
        assert item.stage == Stage.TRIAGE
        assert not item.done


def test_auto_complete_intake_described() -> None:
    request = make_request(description="A change", requester="alice")
    completed = auto_complete(request)
    assert "intake.described" in completed
    # A second run does not re-complete already-done items.
    assert auto_complete(request) == []


def test_auto_complete_triage_and_artifacts_and_decisions() -> None:
    request = make_request(
        description="d",
        requester="bob",
        impacted_domains=["finance"],
        classification=Classification.MEDIUM,
    )
    request.stakeholders.append(Stakeholder(name="Eve", concerns=["cost"]))
    request.artifacts.append(
        Artifact(kind=ArtifactKind.STAKEHOLDER_MAP, name="map", path="/tmp/map.xml")
    )
    request.artifacts.append(
        Artifact(kind=ArtifactKind.PSA_DOCUMENT, name="psa", path="/tmp/psa.md")
    )
    request.artifacts.append(
        Artifact(kind=ArtifactKind.ARCHIMATE_EXPORT, name="export", path="/tmp/export.xml")
    )
    request.decisions.append(Decision(title="Use SaaS"))

    completed = set(auto_complete(request))
    assert {
        "intake.described",
        "triage.classified",
        "triage.domains",
        "sa.stakeholders",
        "sa.map",
        "draft.psa",
        "draft.decisions",
        "pub.published",
    } <= completed


def test_auto_complete_requires_stakeholder_with_concern() -> None:
    request = make_request()
    request.stakeholders.append(Stakeholder(name="Silent"))  # no concerns
    assert "sa.stakeholders" not in auto_complete(request)


def test_gate_blocks_intake_with_readable_reason() -> None:
    request = make_request()  # no description/requester
    ok, reasons = gate_for(request)
    assert not ok
    assert any("intake.described" in reason for reason in reasons)


def test_gate_triage_needs_classification_and_domains() -> None:
    request = make_request(description="d", requester="r")
    auto_complete(request)
    request.stage = Stage.TRIAGE
    ok, reasons = gate_for(request)
    assert not ok
    joined = " ".join(reasons).lower()
    assert "classification" in joined
    assert "domain" in joined

    request.classification = Classification.LARGE
    request.impacted_domains = ["hr"]
    auto_complete(request)
    ok, reasons = gate_for(request)
    assert ok, reasons


def test_gate_peer_review_requires_approval() -> None:
    request = make_request()
    request.stage = Stage.PEER_REVIEW
    ok, reasons = gate_for(request)
    assert not ok
    assert any("approv" in reason.lower() for reason in reasons)

    request.reviews.append(
        Review(reviewer="carol", verdict=ReviewVerdict.APPROVE, stage=Stage.PEER_REVIEW)
    )
    ok, _ = gate_for(request)
    assert ok


def test_gate_peer_review_blocked_by_changes_after_approval() -> None:
    request = make_request()
    request.stage = Stage.PEER_REVIEW
    t1 = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
    request.reviews.append(
        Review(
            reviewer="carol",
            verdict=ReviewVerdict.APPROVE,
            stage=Stage.PEER_REVIEW,
            created_at=t1,
        )
    )
    request.reviews.append(
        Review(
            reviewer="dave",
            verdict=ReviewVerdict.REQUEST_CHANGES,
            stage=Stage.PEER_REVIEW,
            created_at=t2,
        )
    )
    ok, reasons = gate_for(request)
    assert not ok
    assert any("changes" in reason.lower() for reason in reasons)

    # A fresh approval after the change request unblocks the gate.
    request.reviews.append(
        Review(
            reviewer="carol",
            verdict=ReviewVerdict.APPROVE,
            stage=Stage.PEER_REVIEW,
            created_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        )
    )
    ok, _ = gate_for(request)
    assert ok


def test_gate_board_approval_needs_approved_decision() -> None:
    request = make_request()
    request.stage = Stage.BOARD_APPROVAL
    ok, reasons = gate_for(request)
    assert not ok
    assert any("approved decision" in reason.lower() for reason in reasons)

    request.decisions.append(
        Decision(title="Go ahead", status=DecisionStatus.APPROVED, decided_by="board")
    )
    ok, _ = gate_for(request)
    assert ok
