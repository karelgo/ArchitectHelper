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

    engine.set_triage(rid, Classification.LARGE, ["finance", "sales"])

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
    engine.record_decision(rid, "Adopt SaaS CRM", rationale="Lower TCO")

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

    # Blocked: approvals recorded at earlier stages don't count for the board.
    blocked = engine.advance(rid)
    assert not blocked.advanced
    engine.record_decision(
        rid, "Board approves CRM renewal", status=DecisionStatus.APPROVED, decided_by="board"
    )

    # BOARD_APPROVAL -> PUBLICATION; stub exports.
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

    engine.set_triage(rid, Classification.SMALL, ["it"])

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


def test_events_for_unknown_id_raises(engine: WorkflowEngine) -> None:
    with pytest.raises(KeyError):
        engine.events_for("missing-id")


def test_add_stakeholder_guarded_and_audited(
    engine: WorkflowEngine, event_log: EventLog
) -> None:
    request = engine.create_request("Guarded SH", description="d", requester="r")
    engine.add_stakeholder(request.id, Stakeholder(name="Early", concerns=["x"]))
    assert any(
        e.type == EventType.STAKEHOLDER_ADDED for e in event_log.for_request(request.id)
    )

    engine.set_triage(request.id, Classification.SMALL, ["it"])
    engine.advance(request.id)  # -> triage
    engine.advance(request.id)  # -> stakeholder_analysis
    engine.add_stakeholder(request.id, Stakeholder(name="During", concerns=["y"]))
    engine.advance(request.id)  # -> drafting

    from archflow.workflow.engine import GuardViolation

    with pytest.raises(GuardViolation, match="stakeholder-analysis"):
        engine.add_stakeholder(request.id, Stakeholder(name="TooLate"))


def test_early_approved_decision_does_not_satisfy_board_gate(
    engine: WorkflowEngine,
) -> None:
    """An APPROVED decision recorded before board approval must not pre-open the gate."""
    request = engine.create_request("Sneaky", description="d", requester="r")
    engine.record_decision(
        request.id, "Pre-approved", status=DecisionStatus.APPROVED, decided_by="mallory"
    )
    engine.set_triage(request.id, Classification.LARGE, ["it"])
    engine.advance(request.id)  # -> triage
    engine.advance(request.id)  # -> stakeholder_analysis
    engine.add_stakeholder(request.id, Stakeholder(name="Eve", concerns=["cost"]))
    engine.advance(request.id)  # -> drafting
    engine.advance(request.id)  # -> peer_review
    engine.record_review(request.id, "carol", ReviewVerdict.APPROVE)
    engine.advance(request.id)  # -> board_approval

    blocked = engine.advance(request.id)
    assert not blocked.advanced
    assert any("board-approval stage" in r for r in blocked.reasons)


def test_regenerated_artifact_replaces_record_for_same_path(tmp_path: Path) -> None:
    """On-exit refresh must not leave two artifact records for one file."""
    db = create_db_engine(f"sqlite:///{tmp_path}/replace.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")

    def fixed_path_action(ctx: AutomationContext) -> list[Artifact]:
        path = ctx.artifacts_dir / ctx.request.id / "map.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("v", encoding="utf-8")
        return [Artifact(kind=ArtifactKind.STAKEHOLDER_MAP, name="map", path=str(path))]

    registry = ActionRegistry()
    registry.register(Stage.STAKEHOLDER_ANALYSIS, fixed_path_action)
    registry.register_exit(Stage.STAKEHOLDER_ANALYSIS, fixed_path_action)
    engine = WorkflowEngine(
        repository=RequestRepository(db), events=EventLog(db),
        actions=registry, settings=settings,
    )
    request = engine.create_request("Replace", description="d", requester="r")
    engine.set_triage(request.id, Classification.SMALL, ["it"])
    engine.advance(request.id)  # -> triage
    engine.advance(request.id)  # -> stakeholder_analysis (on-enter generates)
    engine.add_stakeholder(request.id, Stakeholder(name="Eve", concerns=["cost"]))
    engine.advance(request.id)  # -> drafting (on-exit regenerates same path)

    loaded = engine.load(request.id)
    map_artifacts = [a for a in loaded.artifacts if a.kind == ArtifactKind.STAKEHOLDER_MAP]
    assert len(map_artifacts) == 1


def test_set_owner_and_stage_timer(engine: WorkflowEngine, event_log: EventLog) -> None:
    request = engine.create_request("Owned", description="d", requester="r")
    first_entered = request.stage_entered_at

    engine.set_owner(request.id, "  a.jansen  ", actor="lead")
    reloaded = engine.load(request.id)
    assert reloaded.owner == "a.jansen"
    assert any(
        e.type == EventType.OWNER_ASSIGNED and e.payload["owner"] == "a.jansen"
        for e in event_log.for_request(request.id)
    )

    engine.advance(request.id)  # -> triage resets the stage clock
    assert engine.load(request.id).stage_entered_at >= first_entered

    engine.set_owner(request.id, "", actor="lead")
    assert engine.load(request.id).owner == ""


def test_record_ai_draft_keeps_latest_per_kind(engine: WorkflowEngine) -> None:
    request = engine.create_request("Drafted", description="d", requester="r")
    engine.record_ai_draft(request.id, "psa", {"markdown": "v1"})
    engine.record_ai_draft(request.id, "psa", {"markdown": "v2"})
    engine.record_ai_draft(request.id, "review", {"summary": "s"})

    reloaded = engine.load(request.id)
    assert set(reloaded.ai_drafts) == {"psa", "review"}
    assert reloaded.ai_drafts["psa"].payload == {"markdown": "v2"}
