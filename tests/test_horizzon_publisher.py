"""Tests for the Horizzon publisher (API push + file-export fallback)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from archflow.config import Settings
from archflow.domain.models import ArchitectureRequest, Stakeholder
from archflow.horizzon.client import HorizzonClient
from archflow.horizzon.publisher import HorizzonPublisher


def make_request() -> ArchitectureRequest:
    return ArchitectureRequest(
        title="CRM renewal",
        description="Replace the aging CRM platform",
        requester="Alice",
        business_goal="Higher customer satisfaction",
        stakeholders=[
            Stakeholder(name="Alice", role="Sales director", concerns=["Adoption risk"]),
            Stakeholder(name="Bob", role="CISO", concerns=["Data protection"]),
        ],
    )


def unconfigured_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, artifacts_dir=tmp_path / "artifacts")  # type: ignore[call-arg]


def configured_settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        artifacts_dir=tmp_path / "artifacts",
        horizzon_base_url="https://acme.horizzon.cloud",
        horizzon_client_id="client-id",
        horizzon_client_secret="s3cret",
    )


def test_unconfigured_falls_back_to_file_export(tmp_path: Path) -> None:
    request = make_request()
    result = HorizzonPublisher(unconfigured_settings(tmp_path)).publish(request)

    assert result.success
    assert result.mode == "file_export"
    assert result.artifact_path is not None
    exported = Path(result.artifact_path)
    assert exported.exists()
    assert "opengroup.org/xsd/archimate/3.0/" in exported.read_text(encoding="utf-8")
    assert "Enterprise Studio" in result.detail


def test_configured_pushes_collection_entities_and_links(tmp_path: Path) -> None:
    request = make_request()
    settings = configured_settings(tmp_path)
    captured: dict[str, Any] = {"entities": [], "links": []}

    def handler(http_request: httpx.Request) -> httpx.Response:
        path = http_request.url.path
        if path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 600})
        if path == "/api/3.0/repositories":
            return httpx.Response(200, json={"_items": [{"id": 7, "name": "Main"}]})
        if path == "/api/3.0/repositories/7/collections" and http_request.method == "GET":
            return httpx.Response(200, json={"_items": []})
        if path == "/api/3.0/repositories/7/collections":
            captured["collection"] = json.loads(http_request.content)
            return httpx.Response(200, json={"id": "col-1"})
        if path == "/api/3.0/repositories/7/collections/col-1/entities/bulk":
            chunk = json.loads(http_request.content)
            captured["entities"].extend(chunk)
            return httpx.Response(200, json={"_items": chunk})
        if path == "/api/3.0/repositories/7/collections/col-1/links/bulk":
            chunk = json.loads(http_request.content)
            captured["links"].extend(chunk)
            return httpx.Response(200, json={"_items": chunk})
        raise AssertionError(f"Unexpected call: {path}")

    client = HorizzonClient(settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    result = HorizzonPublisher(settings, client=client).publish(request)

    assert result.mode == "api"
    assert result.success
    # File export still written: views only travel by file.
    assert result.artifact_path is not None and Path(result.artifact_path).exists()

    assert captured["collection"]["externalId"] == f"archflow-{request.id}"
    entity_types = {e["type"] for e in captured["entities"]}
    assert "ArchiMate:Stakeholder" in entity_types
    assert "ArchiMate:Driver" in entity_types
    assert all(e["externalId"].startswith(f"archflow-{request.id}-") for e in captured["entities"])
    assert captured["links"], "expected association links to be pushed"
    externals = {e["externalId"] for e in captured["entities"]}
    for link in captured["links"]:
        assert link["fromExternalId"] in externals
        assert link["toExternalId"] in externals


def test_api_failure_degrades_to_file_export(tmp_path: Path) -> None:
    request = make_request()
    settings = configured_settings(tmp_path)

    def handler(http_request: httpx.Request) -> httpx.Response:
        if http_request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 600})
        return httpx.Response(500, text="boom")

    client = HorizzonClient(settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    result = HorizzonPublisher(settings, client=client).publish(request)

    assert result.success
    assert result.mode == "file_export"
    assert "API publish failed" in result.detail
    assert result.artifact_path is not None and Path(result.artifact_path).exists()


def test_explicit_repository_id_skips_lookup(tmp_path: Path) -> None:
    request = make_request()
    settings = configured_settings(tmp_path)
    paths: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        path = http_request.url.path
        paths.append(path)
        if path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 600})
        if path.endswith("/collections"):
            return httpx.Response(200, json={"id": "col-9"})
        return httpx.Response(200, json={"_items": []})

    client = HorizzonClient(settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    result = HorizzonPublisher(settings, client=client).publish(request, repository_id=42)

    assert result.mode == "api"
    assert "/api/3.0/repositories" not in paths  # no repository listing
    assert any(p == "/api/3.0/repositories/42/collections" for p in paths)


def test_republish_replaces_existing_collection(tmp_path: Path) -> None:
    """Second publish of the same request deletes the prior collection first."""
    request = make_request()
    settings = configured_settings(tmp_path)
    deleted: list[str] = []
    entity_ids: list[set[str]] = [set(), set()]
    publish_round = {"n": 0}

    def handler(http_request: httpx.Request) -> httpx.Response:
        path = http_request.url.path
        if path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "jwt", "expires_in": 600})
        if path == "/api/3.0/repositories":
            return httpx.Response(200, json={"_items": [{"id": 7}]})
        if path == "/api/3.0/repositories/7/collections" and http_request.method == "GET":
            if publish_round["n"] == 0:
                return httpx.Response(200, json={"_items": []})
            return httpx.Response(
                200,
                json={"_items": [{"id": "col-old", "externalId": f"archflow-{request.id}"}]},
            )
        if path == "/api/3.0/repositories/7/collections/col-old" and http_request.method == "DELETE":
            deleted.append("col-old")
            return httpx.Response(204)
        if path == "/api/3.0/repositories/7/collections":
            return httpx.Response(200, json={"id": f"col-{publish_round['n']}"})
        if path.endswith("/entities/bulk"):
            chunk = json.loads(http_request.content)
            entity_ids[publish_round["n"]].update(e["externalId"] for e in chunk)
            return httpx.Response(200, json={"_items": chunk})
        if path.endswith("/links/bulk"):
            return httpx.Response(200, json={"_items": []})
        raise AssertionError(f"Unexpected call: {http_request.method} {path}")

    client = HorizzonClient(settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    publisher = HorizzonPublisher(settings, client=client)

    assert publisher.publish(request).mode == "api"
    publish_round["n"] = 1
    assert publisher.publish(request).mode == "api"

    assert deleted == ["col-old"], "prior collection must be replaced"
    # externalIds derive from type+name, so re-publishing is idempotent.
    assert entity_ids[0] == entity_ids[1]
