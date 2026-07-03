"""Tests for the governance assistants: drafts, apply_analysis, REST routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archflow.api.assistant import build_assistant_router
from archflow.assistant.copilot import CopilotUnavailable
from archflow.assistant.governance import (
    GovernanceAssistant,
    StakeholderProposal,
    proposal_to_domain,
    request_brief,
)
from archflow.config import Settings
from archflow.domain.events import EventType
from archflow.domain.models import (
    Artifact,
    ArtifactKind,
    Attitude,
    Classification,
    InfluenceLevel,
    ReviewVerdict,
    Stage,
    Stakeholder,
)
from archflow.storage import EventLog, RequestRepository, create_db_engine
from archflow.workflow.actions import ActionRegistry, AutomationContext
from archflow.workflow.engine import GuardViolation, WorkflowEngine

# -- fakes (same shape as tests/test_assistant.py) -----------------------------------


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    id: str = "toolu_1"
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeResponse:
    content: list[FakeBlock]
    stop_reason: str = "end_turn"


class ScriptedMessages:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.requests.append(dict(kwargs))
        return self._responses.pop(0)


def settings(tmp_path: Path | None = None) -> Settings:
    kwargs: dict[str, Any] = {}
    if tmp_path is not None:
        kwargs["artifacts_dir"] = tmp_path / "artifacts"
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def stub_registry() -> ActionRegistry:
    def psa(ctx: AutomationContext) -> list[Artifact]:
        return [Artifact(kind=ArtifactKind.PSA_DOCUMENT, name="psa", path="/tmp/psa.md")]

    def map_(ctx: AutomationContext) -> list[Artifact]:
        return [Artifact(kind=ArtifactKind.STAKEHOLDER_MAP, name="map", path="/tmp/map.xml")]

    registry = ActionRegistry()
    registry.register(Stage.STAKEHOLDER_ANALYSIS, map_)
    registry.register(Stage.DRAFTING, psa)
    return registry


def make_engine(tmp_path: Path) -> WorkflowEngine:
    db = create_db_engine(f"sqlite:///{tmp_path}/gov.db")
    return WorkflowEngine(
        RequestRepository(db),
        EventLog(db),
        actions=stub_registry(),
        settings=settings(tmp_path),
    )


PROPOSAL_PAYLOAD: dict[str, Any] = {
    "stakeholders": [
        {
            "name": "CISO",
            "role": "Security officer",
            "concerns": ["Data residency"],
            "influence": "high",
            "interest": "medium",
            "attitude": "critical",
            "rationale": "Owns the security policy the change touches.",
        }
    ],
    "drivers": [{"name": "Compliance", "description": "GDPR"}],
    "goals": [{"name": "Zero findings", "description": ""}],
    "assessments": [{"name": "Legacy CRM is EOL"}],
    "notes": "Assumed EU hosting.",
}


# -- request_brief / proposal_to_domain ----------------------------------------------


def test_request_brief_serializes_the_request(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = engine.create_request(
        "CRM renewal",
        description="Replace the CRM",
        requester="Alice",
        business_goal="Happier customers",
        impacted_domains=["sales"],
        stakeholders=[Stakeholder(name="Bob", role="Sales lead", concerns=["Adoption"])],
    )
    brief = request_brief(request)
    assert "Title: CRM renewal" in brief
    assert "Bob (Sales lead)" in brief
    assert "Stage: intake" in brief
    assert "sales" in brief


def test_proposal_to_domain_maps_enums() -> None:
    proposal = StakeholderProposal.model_validate(PROPOSAL_PAYLOAD)
    stakeholders, drivers, goals, assessments = proposal_to_domain(proposal)
    assert stakeholders[0].influence is InfluenceLevel.HIGH
    assert stakeholders[0].attitude is Attitude.CRITICAL
    assert drivers[0].name == "Compliance"
    assert goals[0].name == "Zero findings"
    assert assessments[0].name == "Legacy CRM is EOL"


# -- GovernanceAssistant ---------------------------------------------------------------


def test_assistant_requires_key_or_injected_client() -> None:
    with pytest.raises(CopilotUnavailable):
        GovernanceAssistant(settings())


def make_request(tmp_path: Path) -> tuple[WorkflowEngine, str]:
    engine = make_engine(tmp_path)
    request = engine.create_request("CRM renewal", description="d", requester="r")
    return engine, request.id


def test_draft_stakeholder_analysis_forces_the_tool(tmp_path: Path) -> None:
    engine, request_id = make_request(tmp_path)
    scripted = ScriptedMessages(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_stakeholder_analysis",
                        input=PROPOSAL_PAYLOAD,
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    assistant = GovernanceAssistant(settings(), messages_client=scripted)
    proposal = assistant.draft_stakeholder_analysis(engine.load(request_id))

    assert proposal.stakeholders[0].name == "CISO"
    assert proposal.notes == "Assumed EU hosting."
    sent = scripted.requests[0]
    assert sent["tool_choice"] == {"type": "tool", "name": "submit_stakeholder_analysis"}
    assert "CRM renewal" in sent["messages"][0]["content"]


def test_draft_stakeholder_analysis_rejects_invalid_payload(tmp_path: Path) -> None:
    engine, request_id = make_request(tmp_path)
    scripted = ScriptedMessages(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_stakeholder_analysis",
                        input={"stakeholders": [{"role": "no name"}]},
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    assistant = GovernanceAssistant(settings(), messages_client=scripted)
    with pytest.raises(ValueError, match="invalid proposal"):
        assistant.draft_stakeholder_analysis(engine.load(request_id))


def test_draft_psa_joins_text_and_rejects_empty(tmp_path: Path) -> None:
    engine, request_id = make_request(tmp_path)
    scripted = ScriptedMessages(
        [FakeResponse(content=[FakeBlock(type="text", text="## Context\nProse.")])]
    )
    assistant = GovernanceAssistant(settings(), messages_client=scripted)
    text = assistant.draft_psa(engine.load(request_id), current_psa="| table |")
    assert text.startswith("## Context")
    assert "| table |" in scripted.requests[0]["messages"][0]["content"]

    empty = GovernanceAssistant(
        settings(),
        messages_client=ScriptedMessages([FakeResponse(content=[])]),
    )
    with pytest.raises(ValueError, match="empty PSA"):
        empty.draft_psa(engine.load(request_id))


def test_pre_review_parses_draft_and_flags_missing_psa(tmp_path: Path) -> None:
    engine, request_id = make_request(tmp_path)
    scripted = ScriptedMessages(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_review",
                        input={
                            "suggested_verdict": "request_changes",
                            "summary": "Missing risk analysis.",
                            "findings": [
                                {"severity": "blocking", "area": "completeness", "message": "No risks."}
                            ],
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    assistant = GovernanceAssistant(settings(), messages_client=scripted)
    draft = assistant.pre_review(engine.load(request_id), psa_text="")

    assert draft.suggested_verdict is ReviewVerdict.REQUEST_CHANGES
    assert draft.findings[0].severity == "blocking"
    assert "no PSA document was found" in scripted.requests[0]["messages"][0]["content"]


# -- WorkflowEngine.apply_analysis -----------------------------------------------------


def test_apply_analysis_dedups_and_audits(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = engine.create_request(
        "R", description="d", requester="r",
        stakeholders=[Stakeholder(name="CISO", concerns=["old concern"])],
    )
    proposal = StakeholderProposal.model_validate(PROPOSAL_PAYLOAD)
    stakeholders, drivers, goals, assessments = proposal_to_domain(proposal)
    stakeholders.append(Stakeholder(name="Works council"))

    after = engine.apply_analysis(
        request.id,
        stakeholders=stakeholders,
        drivers=drivers,
        goals=goals,
        assessments=assessments,
        actor="alice",
    )
    # "CISO" already existed (case-insensitive dedup) — only the new name lands.
    assert [s.name for s in after.stakeholders] == ["CISO", "Works council"]
    assert after.stakeholders[0].concerns == ["old concern"], "existing entry untouched"
    assert [d.name for d in after.drivers] == ["Compliance"]
    assert [g.name for g in after.goals] == ["Zero findings"]
    assert [a.name for a in after.assessments] == ["Legacy CRM is EOL"]

    events = engine.events_for(request.id)
    applied = [e for e in events if e.type == EventType.ANALYSIS_APPLIED]
    assert len(applied) == 1
    assert applied[0].actor == "alice"
    assert applied[0].payload["stakeholders"] == 1


def test_apply_analysis_guarded_after_stakeholder_stage(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = engine.create_request(
        "R", description="d", requester="r",
        stakeholders=[Stakeholder(name="A", concerns=["x"])],
    )
    engine.set_triage(request.id, Classification.SMALL, ["it"])
    engine.advance(request.id)  # -> triage
    engine.advance(request.id)  # -> stakeholder_analysis
    engine.advance(request.id)  # -> drafting
    with pytest.raises(GuardViolation, match="stakeholder-analysis"):
        engine.apply_analysis(request.id, stakeholders=[Stakeholder(name="Late")])


# -- REST routes -------------------------------------------------------------------


def make_client(
    tmp_path: Path,
    assistant: GovernanceAssistant | None = None,
) -> tuple[TestClient, WorkflowEngine]:
    engine = make_engine(tmp_path)
    app = FastAPI()
    factory = (lambda: assistant) if assistant is not None else None
    app.include_router(build_assistant_router(engine, settings(tmp_path), factory))
    return TestClient(app), engine


def scripted_assistant(responses: list[FakeResponse]) -> GovernanceAssistant:
    return GovernanceAssistant(settings(), messages_client=ScriptedMessages(responses))


def test_route_draft_stakeholders(tmp_path: Path) -> None:
    assistant = scripted_assistant(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_stakeholder_analysis",
                        input=PROPOSAL_PAYLOAD,
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    client, engine = make_client(tmp_path, assistant)
    request = engine.create_request("R", description="d", requester="r")

    response = client.post(f"/requests/{request.id}/assistant/stakeholders")
    assert response.status_code == 200
    assert response.json()["stakeholders"][0]["name"] == "CISO"


def test_route_apply_stakeholders_counts_new_items(tmp_path: Path) -> None:
    client, engine = make_client(tmp_path, scripted_assistant([]))
    request = engine.create_request(
        "R", description="d", requester="r", stakeholders=[Stakeholder(name="CISO")]
    )
    response = client.post(
        f"/requests/{request.id}/assistant/stakeholders/apply",
        json={"proposal": PROPOSAL_PAYLOAD, "actor": "alice"},
    )
    assert response.status_code == 200
    # CISO deduped away; one driver, one goal, one assessment added.
    assert response.json() == {
        "stakeholders": 0,
        "drivers": 1,
        "goals": 1,
        "assessments": 1,
    }


def test_route_apply_is_guarded(tmp_path: Path) -> None:
    client, engine = make_client(tmp_path, scripted_assistant([]))
    request = engine.create_request(
        "R", description="d", requester="r",
        stakeholders=[Stakeholder(name="A", concerns=["x"])],
    )
    engine.set_triage(request.id, Classification.SMALL, ["it"])
    for _ in range(3):  # -> triage -> stakeholder_analysis -> drafting
        engine.advance(request.id)
    response = client.post(
        f"/requests/{request.id}/assistant/stakeholders/apply",
        json={"proposal": PROPOSAL_PAYLOAD},
    )
    assert response.status_code == 409


def test_route_psa_save_writes_artifact(tmp_path: Path) -> None:
    assistant = scripted_assistant(
        [FakeResponse(content=[FakeBlock(type="text", text="## Context\nProse.")])]
    )
    client, engine = make_client(tmp_path, assistant)
    request = engine.create_request("CRM renewal", description="d", requester="r")

    response = client.post(f"/requests/{request.id}/assistant/psa", json={"save": True})
    assert response.status_code == 200
    body = response.json()
    assert body["markdown"].startswith("# Project Start Architecture: CRM renewal")
    saved = Path(body["saved_path"])
    assert saved.read_text(encoding="utf-8") == body["markdown"]

    reloaded = engine.load(request.id)
    artifact = reloaded.artifact_of_kind(ArtifactKind.PSA_DOCUMENT)
    assert artifact is not None and artifact.path == str(saved)
    assert "(AI draft)" in artifact.name


def test_route_psa_saves_provided_markdown_without_generating(tmp_path: Path) -> None:
    """The UI previews a draft, the human approves — saving must not regenerate."""
    client, engine = make_client(tmp_path, scripted_assistant([]))  # no responses scripted
    request = engine.create_request("R", description="d", requester="r")

    response = client.post(
        f"/requests/{request.id}/assistant/psa",
        json={"save": True, "markdown": "# PSA\n\nApproved by a human."},
    )
    assert response.status_code == 200
    saved = Path(response.json()["saved_path"])
    assert saved.read_text(encoding="utf-8") == "# PSA\n\nApproved by a human."


def test_route_review_returns_draft(tmp_path: Path) -> None:
    assistant = scripted_assistant(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_review",
                        input={"suggested_verdict": "approve", "summary": "Solid."},
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    client, engine = make_client(tmp_path, assistant)
    request = engine.create_request("R", description="d", requester="r")

    response = client.post(f"/requests/{request.id}/assistant/review")
    assert response.status_code == 200
    assert response.json()["suggested_verdict"] == "approve"
    # The draft is never recorded on the request — a human records the verdict.
    assert engine.load(request.id).reviews == []


def test_route_503_without_key_and_404_unknown_request(tmp_path: Path) -> None:
    client, engine = make_client(tmp_path, assistant=None)  # no key, no factory
    request = engine.create_request("R", description="d", requester="r")
    assert client.post(f"/requests/{request.id}/assistant/stakeholders").status_code == 503

    with_assistant, _ = make_client(tmp_path, scripted_assistant([]))
    assert with_assistant.post("/requests/nope/assistant/stakeholders").status_code == 404


def test_drafts_are_persisted_and_listable(tmp_path: Path) -> None:
    assistant = scripted_assistant(
        [
            FakeResponse(
                content=[
                    FakeBlock(
                        type="tool_use",
                        name="submit_stakeholder_analysis",
                        input=PROPOSAL_PAYLOAD,
                    )
                ],
                stop_reason="tool_use",
            )
        ]
    )
    client, engine = make_client(tmp_path, assistant)
    request = engine.create_request("R", description="d", requester="r")

    assert client.get(f"/requests/{request.id}/assistant/drafts").json() == {}
    client.post(f"/requests/{request.id}/assistant/stakeholders")
    drafts = client.get(f"/requests/{request.id}/assistant/drafts").json()
    assert set(drafts) == {"stakeholders"}
    assert drafts["stakeholders"]["payload"]["stakeholders"][0]["name"] == "CISO"
