"""Tests for OAuth2 client-credentials auth against Horizzon."""

from __future__ import annotations

import httpx
import pytest

from archflow.config import Settings
from archflow.horizzon.auth import HorizzonAuth, HorizzonAuthError


def make_settings(**overrides: str) -> Settings:
    values: dict[str, str] = {
        "horizzon_base_url": "https://acme.horizzon.cloud",
        "horizzon_client_id": "client-id",
        "horizzon_client_secret": "s3cret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def client_with(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_token_url_defaults_to_oauth_token() -> None:
    auth = HorizzonAuth(make_settings())
    assert auth.token_url == "https://acme.horizzon.cloud/oauth/token"


def test_explicit_token_url_wins() -> None:
    auth = HorizzonAuth(make_settings(horizzon_token_url="https://sso.acme.example/token"))
    assert auth.token_url == "https://sso.acme.example/token"


def test_get_token_posts_client_credentials_form() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content-type"] = request.headers["content-type"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "jwt-1", "expires_in": 600})

    auth = HorizzonAuth(make_settings())
    with client_with(httpx.MockTransport(handler)) as client:
        token = auth.get_token(client)

    assert token == "jwt-1"
    assert seen["url"] == "https://acme.horizzon.cloud/oauth/token"
    assert "application/x-www-form-urlencoded" in seen["content-type"]
    assert "grant_type=client_credentials" in seen["body"]
    assert "client_id=client-id" in seen["body"]
    assert "client_secret=s3cret" in seen["body"]


def test_token_is_cached_until_expiry() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"access_token": f"jwt-{calls['count']}", "expires_in": 600})

    auth = HorizzonAuth(make_settings())
    with client_with(httpx.MockTransport(handler)) as client:
        assert auth.get_token(client) == "jwt-1"
        assert auth.get_token(client) == "jwt-1"
    assert calls["count"] == 1


def test_invalidate_forces_refresh() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"access_token": f"jwt-{calls['count']}", "expires_in": 600})

    auth = HorizzonAuth(make_settings())
    with client_with(httpx.MockTransport(handler)) as client:
        assert auth.get_token(client) == "jwt-1"
        auth.invalidate()
        assert auth.get_token(client) == "jwt-2"


def test_401_raises_credentials_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))
    auth = HorizzonAuth(make_settings())
    with client_with(transport) as client, pytest.raises(HorizzonAuthError, match="credentials"):
        auth.get_token(client)


def test_403_raises_license_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={}))
    auth = HorizzonAuth(make_settings())
    with client_with(transport) as client, pytest.raises(HorizzonAuthError, match="license"):
        auth.get_token(client)
