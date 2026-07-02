"""Tests for the workflow engine using a stub action registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from archflow.config import Settings
from archflow.domain.events import EventType
from archflow.domain.models import (
    Artifact,
    ArtifactKind,
    ChecklistItem,
    Classification,
    DecisionStatus,
    ReviewVerdict,
    Stage,
    Stakeholder,
)
from archflow.storage import EventLog, RequestRepository, create_db_engine
from archflow.workflow import ActionRegistry, WorkflowEngine
from archflow.workflow.actions import AutomationContext


def stub_registry() -> ActionRegistry:
    """Fake artifact-producing actions — no cross-module imports."""

    def fake(kind: ArtifactKind, filename: str):
        def action(ctx: AutomationContext) -> list[Artifact]:
            directory = ctx.artifacts_dir / ctx.request.id
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            path.write_text("stub", encoding="utf-8")
            return [Artifact(kind=kind, name=f"stub {kind.value}", path=str(path))]

        return action

    registry = ActionRegistry()
    registry.register(
        Stage.STAKEHOLDER_ANALYSIS, fake(ArtifactKind.STAKEHOLDER_MAP, "map.xml")
    )
    registry.register(Stage.DRAFTING, fake(ArtifactKind.PSA_DOCUMENT, "psa.md"))
    registry.register(Stage.PUBLICATION, fake(ArtifactKind.ARCHIMATE_EXPORT, "export.xml"))
    return registry


@pytest.fixture()
def engine(tmp_path: Path) -> WorkflowEngine:
    db_engine = create_db_engine(f"sqlite:///{tmp_path}/t.db")
    repo = RequestRepository(db_engine)
    events = EventLog(db_engine)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t.db", artifacts_dir=tmp_path / "artifacts"
    )
    return WorkflowEngine(repo, events, actions=stub_registry(), settings=settings)


@pytest.fixture()
def event_log(engine: WorkflowEngine) -> EventLog:
    return engine._events  # noqa: SLF001 - test peeks at the wired log


def test_create_request_seeds_checklist_and_events(
    engine: WorkflowEngine, event_log: EventLog
) -> None:
    request = engine.create_request("New CRM", description="Replace CRM", requester="alice")
    assert request.stage == Stage.INTAKE
    keys = {i.key for i in request.checklist}
    assert {"intake.described", "triage.classified", "sa.map", "draft.psa", "pub.published"} <= keys
    # intake.described was auto-completed at creation.
    assert next(i for i in request.checklist if i.key == "intake.described").done

    types = [e.type for e in event_log.for_request(request.id)]
    assert EventType.REQUEST_CREATED in types
    assert EventType.STAGE_ENTERED in types


def test_full_track_walks_pipeline_to_done(engine: WorkflowEngine, event_log: EventLog) -> None:
    request = engine.create_request("Big change", description="Major", requester="bob")
    rid = request.id

    # INTAKE -> TRIAGE
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.TRIAGE

    # TRIAGE blocked until classified.
    blocked = engine.advance(rid)
    assert not blocked.advanced
    assert any("classification" in r.lower() for r in blocked.reasons)

    loaded = engine.get(rid)
    assert loaded is not None
    loaded.classification = Classification.LARGE
    loaded.impacted_domains = ["finance", "sales"]
    engine._repo.save(loaded)  # noqa: SLF001 - classification set out-of-band

    # TRIAGE -> STAKEHOLDER_ANALYSIS (stub generates the stakeholder map).
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.STAKEHOLDER_ANALYSIS
    assert [a.kind for a in result.generated] == [ArtifactKind.STAKEHOLDER_MAP]
    assert Path(result.generated[0].path).exists()

    # Blocked until a stakeholder with concerns exists.
    blocked = engine.advance(rid)
    assert not blocked.advanced
    engine.add_stakeholder(rid, Stakeholder(name="Eve", role="CFO", concerns=["cost"]))

    # STAKEHOLDER_ANALYSIS -> DRAFTING (stub generates the PSA).
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.DRAFTING
    assert [a.kind for a in result.generated] == [ArtifactKind.PSA_DOCUMENT]

    # Blocked until a decision is recorded.
    blocked = engine.advance(rid)
    assert not blocked.advanced
    assert any("draft.decisions" in r for r in blocked.reasons)
    engine.record_decision(
        rid, "Adopt SaaS CRM", rationale="Lower TCO", status=DecisionStatus.APPROVED,
        decided_by="board",
    )

    # DRAFTING -> PEER_REVIEW.
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.PEER_REVIEW

    # Blocked until an approving review exists.
    blocked = engine.advance(rid)
    assert not blocked.advanced
    assert any("approv" in r.lower() for r in blocked.reasons)
    engine.record_review(rid, "carol", ReviewVerdict.APPROVE, comments="LGTM")

    # PEER_REVIEW -> BOARD_APPROVAL (large request: no fast track).
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.BOARD_APPROVAL

    # BOARD_APPROVAL -> PUBLICATION (decision already approved); stub exports.
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.PUBLICATION
    assert [a.kind for a in result.generated] == [ArtifactKind.ARCHIMATE_EXPORT]

    # PUBLICATION -> DONE.
    result = engine.advance(rid)
    assert result.advanced and result.to_stage == Stage.DONE

    # Terminal stage: no further advancing.
    result = engine.advance(rid)
    assert not result.advanced
    assert any("terminal" in r.lower() for r in result.reasons)

    types = {e.type for e in event_log.for_request(rid)}
    assert {
        EventType.REQUEST_CREATED,
        EventType.STAGE_ENTERED,
        EventType.STAGE_BLOCKED,
        EventType.ARTIFACT_GENERATED,
        EventType.REVIEW_RECORDED,
        EventType.DECISION_RECORDED,
    } <= types


def test_fast_track_skips_board_approval(engine: WorkflowEngine) -> None:
    request = engine.create_request("Tiny tweak", description="Small", requester="dan")
    rid = request.id
    engine.advance(rid)  # -> TRIAGE

    loaded = engine.get(rid)
    assert loaded is not None
    loaded.classification = Classification.SMALL
    loaded.impacted_domains = ["it"]
    engine._repo.save(loaded)  # noqa: SLF001

    engine.advance(rid)  # -> STAKEHOLDER_ANALYSIS
    engine.add_stakeholder(rid, Stakeholder(name="Ops", concerns=["availability"]))
    engine.advance(rid)  # -> DRAFTING
    engine.record_decision(rid, "Patch it", status=DecisionStatus.APPROVED)
    engine.advance(rid)  # -> PEER_REVIEW
    engine.record_review(rid, "carol", ReviewVerdict.APPROVE)

    result = engine.advance(rid)
    assert result.advanced
    assert result.from_stage == Stage.PEER_REVIEW
    assert result.to_stage == Stage.PUBLICATION  # BOARD_APPROVAL skipped


def test_reject_flow(engine: WorkflowEngine, event_log: EventLog) -> None:
    request = engine.create_request("Doomed", description="x", requester="y")
    rejected = engine.reject(request.id, actor="board", reason="Out of scope")
    assert rejected.stage == Stage.REJECTED

    result = engine.advance(request.id)
    assert not result.advanced
    assert any("terminal" in r.lower() for r in result.reasons)

    events = event_log.for_request(request.id)
    rejection = next(e for e in events if e.type == EventType.REQUEST_REJECTED)
    assert rejection.actor == "board"
    assert rejection.payload["reason"] == "Out of scope"


def test_complete_item_guards_and_custom_items(
    engine: WorkflowEngine, event_log: EventLog
) -> None:
    request = engine.create_request("Manual", description="d", requester="r")

    # Auto-conditioned items cannot be completed by hand (gate bypass guard).
    with pytest.raises(ValueError, match="automatically"):
        engine.complete_item(request.id, "triage.domains", actor="mallory")

    # Custom items belonging to a future stage are also out of reach.
    request.checklist.append(
        ChecklistItem(key="custom.later", description="Later sign-off", stage=Stage.TRIAGE)
    )
    request.checklist.append(
        ChecklistItem(key="custom.now", description="Security sign-off", stage=Stage.INTAKE)
    )
    engine._repo.save(request)  # noqa: SLF001 - seeding custom items out-of-band
    with pytest.raises(ValueError, match="stage"):
        engine.complete_item(request.id, "custom.later", actor="ea-team")

    # A custom item of the current stage completes fine and emits an event.
    updated = engine.complete_item(request.id, "custom.now", actor="ea-team")
    item = next(i for i in updated.checklist if i.key == "custom.now")
    assert item.done and item.completed_by == "ea-team"
    assert any(
        e.type == EventType.CHECKLIST_COMPLETED for e in event_log.for_request(request.id)
    )

    with pytest.raises(KeyError):
        engine.complete_item(request.id, "no.such.key", actor="ea-team")


def test_set_triage_rejected_after_triage_stage(engine: WorkflowEngine) -> None:
    """Reclassifying to small later must not bypass board approval."""
    request = engine.create_request("Guarded", description="d", requester="r")
    engine.set_triage(request.id, Classification.LARGE, ["hr"])  # fine at intake
    engine.advance(request.id)  # -> triage
    engine.advance(request.id)  # -> stakeholder_analysis
    with pytest.raises(ValueError, match="[Tt]riage"):
        engine.set_triage(request.id, Classification.SMALL)


def test_unknown_request_id_raises(engine: WorkflowEngine) -> None:
    with pytest.raises(KeyError):
        engine.advance("missing-id")


def test_on_exit_refreshes_stage_artifacts(tmp_path: Path) -> None:
    """Artifacts regenerate when leaving a stage, so work added during the
    stage (stakeholders, decisions) lands in the recorded deliverable."""
    db = create_db_engine(f"sqlite:///{tmp_path}/exit.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")
    calls: list[str] = []

    def tracker(label: str):
        def action(ctx: AutomationContext) -> list[Artifact]:
            calls.append(label)
            return []

        return action

    registry = ActionRegistry()
    registry.register(Stage.TRIAGE, tracker("enter:triage"))
    registry.register_exit(Stage.TRIAGE, tracker("exit:triage"))
    engine = WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        actions=registry,
        settings=settings,
    )
    request = engine.create_request("Exit hooks", description="d", requester="r")
    engine.advance(request.id)  # intake -> triage: on-enter fires
    assert calls == ["enter:triage"]
    engine.set_triage(request.id, Classification.SMALL, ["hr"])
    engine.advance(request.id)  # triage -> stakeholder_analysis: on-exit fires
    assert calls == ["enter:triage", "exit:triage"]
