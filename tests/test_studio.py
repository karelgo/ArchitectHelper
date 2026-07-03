"""Tests for Studio view projects: service, storage, and REST routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from archflow.api.app import create_app
from archflow.archimate.openexchange import read_model
from archflow.assistant.copilot import ArchiMateCopilot, CopilotReply
from archflow.config import Settings, get_settings
from archflow.domain.models import Stakeholder
from archflow.storage import EventLog, RequestRepository, ViewProjectRepository, create_db_engine
from archflow.studio.service import StudioService
from archflow.workflow.actions import ActionRegistry
from archflow.workflow.engine import WorkflowEngine


@pytest.fixture()
def studio(tmp_path: Path) -> StudioService:
    db = create_db_engine(f"sqlite:///{tmp_path}/studio.db")
    return StudioService(ViewProjectRepository(db))


@pytest.fixture()
def client(tmp_path: Path, studio: StudioService, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ARCHFLOW_DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    get_settings.cache_clear()
    db = create_db_engine(f"sqlite:///{tmp_path}/app.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")  # type: ignore[call-arg]
    engine = WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        actions=ActionRegistry(),  # no automation needed here
        settings=settings,
    )
    test_client = TestClient(create_app(engine, studio=studio))
    yield test_client
    get_settings.cache_clear()


def test_service_create_and_roundtrip(studio: StudioService) -> None:
    project = studio.create("Landscape", "our estate")
    loaded = studio.get(project.id)
    assert loaded.name == "Landscape"
    assert loaded.model.name == "Landscape"

    loaded.model.add_element("ApplicationComponent", "CRM")
    loaded.bump_model()
    studio.save(loaded)
    assert len(studio.get(project.id).model.elements) == 1

    studio.delete(project.id)
    with pytest.raises(KeyError):
        studio.get(project.id)


def test_drawio_document_caches_until_model_changes(studio: StudioService) -> None:
    project = studio.create("Cache", "")
    project.model.add_element("Goal", "G1")
    project.bump_model()
    studio.save(project)

    first = studio.drawio_document(project.id)
    assert "mxfile" in first and "G1" in first
    assert studio.drawio_document(project.id) == first  # served from cache

    loaded = studio.get(project.id)
    loaded.model.add_element("Goal", "G2")
    loaded.bump_model()
    studio.save(loaded)
    assert "G2" in studio.drawio_document(project.id)  # re-rendered


def test_save_drawio_document_syncs_geometry(studio: StudioService) -> None:
    project = studio.create("Sync", "")
    element = project.model.add_element("Goal", "Move me")
    project.bump_model()
    studio.save(project)

    xml = studio.drawio_document(project.id)
    moved = xml.replace('x="40"', 'x="640"', 1)
    assert studio.save_drawio_document(project.id, moved) >= 1

    loaded = studio.get(project.id)
    node = next(n for n in loaded.model.views[0].nodes if n.element_ref == element.id)
    assert node.x == 640
    # The user's edited document is what the editor gets back.
    assert studio.drawio_document(project.id) == moved


def test_routes_crud_and_exchange(client: TestClient) -> None:
    created = client.post("/studio/views", json={"name": "API View", "description": "d"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    assert any(v["id"] == project_id for v in client.get("/studio/views").json())
    assert client.get(f"/studio/views/{project_id}").status_code == 200
    assert client.get("/studio/views/nope").status_code == 404

    patched = client.patch(f"/studio/views/{project_id}", json={"name": "Renamed"})
    assert patched.json()["name"] == "Renamed"

    drawio = client.get(f"/studio/views/{project_id}/drawio")
    assert drawio.status_code == 200 and "mxfile" in drawio.json()["xml"]

    exchange = client.get(f"/studio/views/{project_id}/exchange")
    assert exchange.status_code == 200
    assert "opengroup.org/xsd/archimate/3.0/" in exchange.text
    assert "attachment" in exchange.headers["content-disposition"]

    assert client.delete(f"/studio/views/{project_id}").status_code == 204
    assert client.get(f"/studio/views/{project_id}").status_code == 404


def test_route_seed_from_request(client: TestClient) -> None:
    request = client.post(
        "/requests",
        json={
            "title": "CRM renewal",
            "description": "d",
            "requester": "Alice",
            "stakeholders": [{"name": "Alice", "concerns": ["Adoption"]}],
        },
    ).json()

    created = client.post(
        "/studio/views", json={"name": "", "request_id": request["id"]}
    )
    assert created.status_code == 201
    body = created.json()
    assert "CRM renewal" in body["name"]
    element_types = {e["type"] for e in body["model"]["elements"]}
    assert {"Stakeholder", "Driver"} <= element_types

    assert client.post("/studio/views", json={"name": "x", "request_id": "nope"}).status_code == 404


def test_route_import_exchange_and_invalid(client: TestClient, studio: StudioService) -> None:
    source = studio.create("Source", "")
    source.model.add_element("Capability", "Pay")
    source.bump_model()
    studio.save(source)
    xml = studio.exchange_document(source.id)

    imported = client.post("/studio/views/import", json={"name": "Imported", "xml": xml})
    assert imported.status_code == 201
    model = read_model(client.get(f"/studio/views/{imported.json()['id']}/exchange").text)
    assert model.elements[0].name == "Pay"

    assert client.post("/studio/views/import", json={"xml": "<not-xml"}).status_code == 422


def test_put_drawio_rejects_garbage(client: TestClient) -> None:
    project_id = client.post("/studio/views", json={"name": "G"}).json()["id"]
    response = client.put(f"/studio/views/{project_id}/drawio", json={"xml": "<broken"})
    assert response.status_code == 422


def test_assistant_route_503_without_key(client: TestClient) -> None:
    project_id = client.post("/studio/views", json={"name": "NoKey"}).json()["id"]
    response = client.post(
        f"/studio/views/{project_id}/assistant", json={"message": "hello"}
    )
    assert response.status_code == 503
    assert "ARCHFLOW_ANTHROPIC_API_KEY" in response.json()["detail"]


def test_assistant_route_passes_request_context(
    tmp_path: Path, studio: StudioService
) -> None:
    """A view linked to a request gets the request brief as copilot context."""
    db = create_db_engine(f"sqlite:///{tmp_path}/ctx.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")  # type: ignore[call-arg]
    engine = WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        actions=ActionRegistry(),
        settings=settings,
    )
    request = engine.create_request("CRM renewal", description="d", requester="r")

    recorded: dict[str, str | None] = {}

    class FakeCopilot:
        def chat(
            self, project: object, message: str, context: str | None = None
        ) -> CopilotReply:
            recorded["context"] = context
            return CopilotReply(reply="ok")

    factory = cast("Callable[[], ArchiMateCopilot]", FakeCopilot)
    test_client = TestClient(create_app(engine, studio=studio, copilot_factory=factory))

    linked = test_client.post(
        "/studio/views", json={"name": "", "request_id": request.id}
    ).json()
    test_client.post(f"/studio/views/{linked['id']}/assistant", json={"message": "hi"})
    assert recorded["context"] is not None and "CRM renewal" in recorded["context"]

    plain = test_client.post("/studio/views", json={"name": "Standalone"}).json()
    test_client.post(f"/studio/views/{plain['id']}/assistant", json={"message": "hi"})
    assert recorded["context"] is None


def test_ui_config_and_index_redirect(client: TestClient) -> None:
    config = client.get("/ui-config").json()
    assert config["drawio_embed_url"].startswith("https://")
    assert config["assistant_available"] is False

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_request_seed_requires_engine_visibility(
    tmp_path: Path, studio: StudioService
) -> None:
    """Stakeholder-map seeding uses the live request from the engine."""
    db = create_db_engine(f"sqlite:///{tmp_path}/seed.db")
    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")  # type: ignore[call-arg]
    engine = WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        actions=ActionRegistry(),
        settings=settings,
    )
    request = engine.create_request("Seeded", description="d", requester="r")
    engine.add_stakeholder(request.id, Stakeholder(name="Zoe", concerns=["risk"]))

    project = studio.create_from_request("", engine.load(request.id))
    names = {e.name for e in project.model.elements}
    assert "Zoe" in names and "risk" in names


def test_route_lint_reports_findings(client: TestClient, studio: StudioService) -> None:
    project = studio.create("Lint me", "")
    actor = project.model.add_element("BusinessActor", "Alice")
    driver = project.model.add_element("Driver", "Cost pressure")
    # Realization pointing at a Driver is illegal — the linter must flag it.
    project.model.add_relationship("Realization", actor.id, driver.id)
    project.bump_model()
    studio.save(project)

    response = client.get(f"/studio/views/{project.id}/lint")
    assert response.status_code == 200
    rules = {finding["rule"] for finding in response.json()}
    assert "unrealizable-target" in rules
    assert response.json()[0]["severity"] == "error", "errors come first"

    assert client.get("/studio/views/nope/lint").status_code == 404


def test_exchange_download_with_non_ascii_name(client: TestClient) -> None:
    """Em dashes and other non-latin-1 characters must not break the header."""
    project_id = client.post(
        "/studio/views", json={"name": "Stakeholder map — Betaalplatform"}
    ).json()["id"]
    response = client.get(f"/studio/views/{project_id}/exchange")
    assert response.status_code == 200
    assert "archimate.xml" in response.headers["content-disposition"]
