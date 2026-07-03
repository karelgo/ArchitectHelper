"""Tests for the REST API (TestClient against an injected engine)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archflow.api.app import create_app
from archflow.config import Settings
from archflow.domain.models import Artifact, ArtifactKind, Stage
from archflow.storage import EventLog, RequestRepository, create_db_engine
from archflow.workflow.actions import ActionRegistry, AutomationContext
from archflow.workflow.engine import WorkflowEngine


def stub_registry() -> ActionRegistry:
    """Actions that fake the artifacts the real automation would produce."""

    def fake(kind: ArtifactKind, name: str):  # noqa: ANN202
        def action(ctx: AutomationContext) -> list[Artifact]:
            return [Artifact(kind=kind, name=name, path=f"/tmp/{name}")]

        return action

    registry = ActionRegistry()
    registry.register(
        Stage.STAKEHOLDER_ANALYSIS, fake(ArtifactKind.STAKEHOLDER_MAP, "map.xml")
    )
    registry.register(Stage.DRAFTING, fake(ArtifactKind.PSA_DOCUMENT, "psa.md"))
    registry.register(Stage.PUBLICATION, fake(ArtifactKind.ARCHIMATE_EXPORT, "export.xml"))
    return registry


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = create_db_engine(f"sqlite:///{tmp_path}/api.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")  # type: ignore[call-arg]
    engine = WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        actions=stub_registry(),
        settings=settings,
    )
    return TestClient(create_app(engine))


PAYLOAD = {
    "title": "CRM renewal",
    "description": "Replace the aging CRM",
    "requester": "Alice",
    "business_goal": "Happier customers",
    "stakeholders": [
        {"name": "Alice", "role": "Sales", "concerns": ["Adoption"]},
    ],
}


def create_request(client: TestClient) -> str:
    response = client.post("/requests", json=PAYLOAD)
    assert response.status_code == 201
    return response.json()["id"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_get_request(client: TestClient) -> None:
    request_id = create_request(client)
    response = client.get(f"/requests/{request_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "CRM renewal"
    assert body["stage"] == "intake"
    assert body["checklist"], "checklist should be seeded"


def test_unknown_request_is_404(client: TestClient) -> None:
    for call in (
        lambda: client.get("/requests/nope"),
        lambda: client.post("/requests/nope/advance", json={}),
        lambda: client.get("/requests/nope/events"),
        lambda: client.post("/requests/nope/reviews", json={"reviewer": "x", "verdict": "approve"}),
    ):
        assert call().status_code == 404


def test_full_process_over_api(client: TestClient) -> None:
    request_id = create_request(client)

    # intake -> triage (intake checklist auto-completes from the payload)
    response = client.post(f"/requests/{request_id}/advance", json={})
    assert response.json()["advanced"], response.json()

    # blocked in triage until classification + domains recorded
    blocked = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert not blocked["advanced"]
    assert any("classif" in r.lower() for r in blocked["reasons"])

    response = client.post(
        f"/requests/{request_id}/triage",
        json={"classification": "medium", "impacted_domains": ["CRM", "Integration"]},
    )
    assert response.status_code == 200

    # triage -> stakeholder_analysis (stub action fakes the map artifact)
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "stakeholder_analysis"
    assert result["generated"][0]["kind"] == "stakeholder_map"

    # -> drafting; blocked until a decision is recorded
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "drafting"
    blocked = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert not blocked["advanced"]
    client.post(
        f"/requests/{request_id}/decisions",
        json={"title": "Buy over build", "rationale": "Speed"},
    )

    # -> peer_review; needs an approving review
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "peer_review"
    client.post(
        f"/requests/{request_id}/reviews",
        json={"reviewer": "Bob", "verdict": "approve", "comments": "LGTM"},
    )

    # -> board_approval; needs an approved decision
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "board_approval"
    client.post(
        f"/requests/{request_id}/decisions",
        json={"title": "Board approves CRM renewal", "status": "approved", "decided_by": "Board"},
    )

    # -> publication (stub export artifact) -> done
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "publication"
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["advanced"] and result["to_stage"] == "done"

    # audit trail recorded the journey
    events = client.get(f"/requests/{request_id}/events").json()
    types = [e["type"] for e in events]
    assert "request_created" in types
    assert types.count("stage_entered") >= 7
    assert "artifact_generated" in types


def test_fast_track_skips_board_approval(client: TestClient) -> None:
    request_id = create_request(client)
    client.post(f"/requests/{request_id}/advance", json={})
    client.post(
        f"/requests/{request_id}/triage",
        json={"classification": "small", "impacted_domains": ["CRM"]},
    )
    client.post(f"/requests/{request_id}/advance", json={})  # -> stakeholder_analysis
    client.post(f"/requests/{request_id}/advance", json={})  # -> drafting
    client.post(f"/requests/{request_id}/decisions", json={"title": "D1"})
    client.post(f"/requests/{request_id}/advance", json={})  # -> peer_review
    client.post(
        f"/requests/{request_id}/reviews", json={"reviewer": "Bob", "verdict": "approve"}
    )
    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert result["to_stage"] == "publication"  # board_approval skipped


def test_reject_and_checklist_endpoints(client: TestClient) -> None:
    request_id = create_request(client)

    response = client.post(
        f"/requests/{request_id}/checklist/nonexistent.key/complete", json={"actor": "t"}
    )
    assert response.status_code == 404

    response = client.post(
        f"/requests/{request_id}/reject", json={"actor": "cto", "reason": "Duplicate"}
    )
    assert response.status_code == 200
    assert response.json()["stage"] == "rejected"

    result = client.post(f"/requests/{request_id}/advance", json={}).json()
    assert not result["advanced"]
    assert "terminal" in result["reasons"][0]


def test_add_stakeholder_endpoint_and_stage_guard(client: TestClient) -> None:
    request_id = create_request(client)

    response = client.post(
        f"/requests/{request_id}/stakeholders",
        json={"name": "Bob", "role": "CISO", "concerns": ["Data protection"]},
    )
    assert response.status_code == 200
    assert any(s["name"] == "Bob" for s in response.json()["stakeholders"])

    assert client.post("/requests/nope/stakeholders", json={"name": "X"}).status_code == 404

    # Walk past stakeholder analysis, then adding is refused with 409.
    client.post(f"/requests/{request_id}/advance", json={})
    client.post(
        f"/requests/{request_id}/triage",
        json={"classification": "small", "impacted_domains": ["CRM"]},
    )
    client.post(f"/requests/{request_id}/advance", json={})  # -> stakeholder_analysis
    client.post(f"/requests/{request_id}/advance", json={})  # -> drafting
    response = client.post(f"/requests/{request_id}/stakeholders", json={"name": "Late"})
    assert response.status_code == 409


def test_auto_completed_checklist_item_is_409_not_404(client: TestClient) -> None:
    request_id = create_request(client)
    response = client.post(
        f"/requests/{request_id}/checklist/triage.classified/complete", json={"actor": "t"}
    )
    assert response.status_code == 409
    assert "automatically" in response.json()["detail"]


def test_gate_report_shows_live_conditions(client: TestClient) -> None:
    """The gate endpoint is advance's dry run — same rules, no side effects."""
    request_id = create_request(client)

    gate = client.get(f"/requests/{request_id}/gate").json()
    assert gate["stage"] == "intake"
    assert gate["next_stage"] == "triage"
    assert gate["ok"] is True  # payload satisfies the intake checklist
    assert gate["conditions"][0]["key"] == "intake.described"
    assert gate["conditions"][0]["met"] is True

    client.post(f"/requests/{request_id}/advance", json={})  # -> triage
    gate = client.get(f"/requests/{request_id}/gate").json()
    assert gate["ok"] is False
    by_key = {c["key"]: c["met"] for c in gate["conditions"]}
    assert by_key == {"triage.classified": False, "triage.domains": False}

    client.post(
        f"/requests/{request_id}/triage",
        json={"classification": "small", "impacted_domains": ["crm"]},
    )
    gate = client.get(f"/requests/{request_id}/gate").json()
    assert gate["ok"] is True and gate["next_stage"] == "stakeholder_analysis"

    assert client.get("/requests/nope/gate").status_code == 404


def test_gate_report_peer_review_condition(client: TestClient) -> None:
    request_id = create_request(client)
    client.post(f"/requests/{request_id}/advance", json={})
    client.post(
        f"/requests/{request_id}/triage",
        json={"classification": "medium", "impacted_domains": ["crm"]},
    )
    for _ in range(2):  # -> stakeholder_analysis -> drafting
        client.post(f"/requests/{request_id}/advance", json={})
    client.post(
        f"/requests/{request_id}/decisions",
        json={"title": "Buy over build", "rationale": "", "status": "proposed", "decided_by": "a"},
    )
    client.post(f"/requests/{request_id}/advance", json={})  # -> peer_review

    gate = client.get(f"/requests/{request_id}/gate").json()
    assert gate["stage"] == "peer_review"
    review_condition = next(c for c in gate["conditions"] if c["key"] == "review.approved")
    assert review_condition["met"] is False

    client.post(
        f"/requests/{request_id}/reviews",
        json={"reviewer": "Bob", "verdict": "approve", "comments": ""},
    )
    gate = client.get(f"/requests/{request_id}/gate").json()
    assert gate["ok"] is True


def test_request_map_svg(client: TestClient) -> None:
    request_id = create_request(client)
    response = client.get(f"/requests/{request_id}/map.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "Alice" in response.text, "stakeholders appear on the rendered map"

    assert client.get("/requests/nope/map.svg").status_code == 404
