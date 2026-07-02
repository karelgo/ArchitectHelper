"""OAuth2 client-credentials authentication for the Bizzdesign Open API.

Horizzon issues JWT bearer tokens from ``https://<org>.horizzon.cloud/oauth/token``
against an *API client* (id + secret) created by an administrator in Horizzon.
There are no OAuth scopes; authorization is the API client's permission flags.
"""

from __future__ import annotations

import time

import httpx

from archflow.config import Settings
from archflow.horizzon.errors import HorizzonAuthError

#: Seconds subtracted from ``expires_in`` so we refresh before actual expiry.
_EXPIRY_MARGIN = 60.0


class HorizzonAuth:
    """Fetches and caches OAuth2 client-credentials tokens."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0  # time.monotonic() deadline

    @property
    def token_url(self) -> str:
        if self._settings.horizzon_token_url:
            return self._settings.horizzon_token_url
        return self._settings.horizzon_base_url.rstrip("/") + "/oauth/token"

    def invalidate(self) -> None:
        """Drop the cached token so the next call fetches a fresh one."""
        self._token = None
        self._expires_at = 0.0

    def get_token(self, client: httpx.Client) -> str:
        """Return a valid bearer token, reusing the cached one when fresh."""
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token

        response = client.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.horizzon_client_id,
                "client_secret": self._settings.horizzon_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 401:
            raise HorizzonAuthError(
                "Horizzon rejected the API client credentials (401). "
                "Check ARCHFLOW_HORIZZON_CLIENT_ID / ARCHFLOW_HORIZZON_CLIENT_SECRET."
            )
        if response.status_code == 403:
            raise HorizzonAuthError(
                "Horizzon returned 403 on the token endpoint: your license tier "
                "does not include the Bizzdesign Open API. Contact your "
                "Bizzdesign administrator or account manager."
            )
        if response.status_code != 200:
            raise HorizzonAuthError(
                f"Token request failed with HTTP {response.status_code}: {response.text[:200]}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise HorizzonAuthError("Token response contained no access_token")
        expires_in = float(payload.get("expires_in", 300))
        self._token = str(token)
        self._expires_at = time.monotonic() + max(expires_in - _EXPIRY_MARGIN, 30.0)
        return self._token
