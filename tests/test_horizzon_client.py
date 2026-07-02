"""Tests for the Bizzdesign Open API client (all HTTP via MockTransport)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from archflow.config import Settings
from archflow.horizzon.client import HorizzonClient, HorizzonError


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        horizzon_base_url="https://acme.horizzon.cloud",
        horizzon_client_id="client-id",
        horizzon_client_secret="s3cret",
    )


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "jwt", "expires_in": 600})


def make_client(handler: Any) -> HorizzonClient:
    return HorizzonClient(make_settings(), transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_requests_carry_bearer_token_and_base_url() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"_items": [{"id": 1, "name": "Repo"}]})

    with make_client(handler) as client:
        repos = client.list_repositories()

    assert repos == [{"id": 1, "name": "Repo"}]
    assert seen["auth"] == "Bearer jwt"
    assert seen["url"] == "https://acme.horizzon.cloud/api/3.0/repositories"


def test_iter_objects_follows_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        assert limit == 2
        pages = {
            0: [{"id": "a"}, {"id": "b"}],
            2: [{"id": "c"}],
        }
        return httpx.Response(200, json={"_items": pages.get(offset, []), "_offset": offset})

    with make_client(handler) as client:
        objects = list(client.iter_objects(7, page_size=2))

    assert [o["id"] for o in objects] == ["a", "b", "c"]


def test_iter_objects_passes_filters() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        seen["params"] = dict(request.url.params)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"_items": []})

    with make_client(handler) as client:
        list(client.iter_objects(7, type="ArchiMate:BusinessActor", updated_after="2026-01-01"))

    assert seen["path"] == "/api/3.0/repositories/7/objects"
    assert seen["params"]["type"] == "ArchiMate:BusinessActor"
    assert seen["params"]["updatedAfter"] == "2026-01-01"
    assert seen["params"]["includeExternalIds"] == "true"


def test_429_is_retried_with_backoff() -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"_items": [{"id": 1}]})

    client = HorizzonClient(
        make_settings(), transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    with client:
        repos = client.list_repositories()

    assert repos == [{"id": 1}]
    assert calls["count"] == 3
    assert sleeps == [0.01, 0.01]


def test_401_refreshes_token_once_then_fails() -> None:
    calls = {"api": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            calls["token"] += 1
            return token_response()
        calls["api"] += 1
        return httpx.Response(401, text="expired")

    with make_client(handler) as client, pytest.raises(HorizzonError, match="401"):
        client.list_repositories()

    assert calls["api"] == 2  # original + one retry with a fresh token
    assert calls["token"] == 2


def test_bulk_create_entities_chunks_requests() -> None:
    bodies: list[list[dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        assert request.url.path == "/api/3.0/repositories/7/collections/col-1/entities/bulk"
        chunk = json.loads(request.content)
        bodies.append(chunk)
        return httpx.Response(200, json={"_items": chunk})

    entities = [{"externalId": f"e{i}"} for i in range(1200)]
    with make_client(handler) as client:
        created = client.bulk_create_entities(7, "col-1", entities)

    assert [len(b) for b in bodies] == [500, 500, 200]
    assert len(created) == 1200


def test_error_response_raises_horizzon_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return token_response()
        return httpx.Response(500, text="boom")

    with make_client(handler) as client, pytest.raises(HorizzonError, match="HTTP 500"):
        client.list_repositories()
