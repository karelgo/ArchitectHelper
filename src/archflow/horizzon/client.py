"""HTTP client for the Bizzdesign Open API v3.0.

Grounded in the official OpenAPI spec
(``https://downloads.bizzdesign.com/Support/api/3.0/Bizzdesign_Open_API_documentation_v3.0.yaml``):

- Base URL: ``https://<org>.horizzon.cloud/api/3.0``
- Reads: repositories, objects, relations (paginated ``{_items, _offset, _limit}``)
- Writes: only *architecture automation* collections / entities / links
  (native Enterprise Studio content and views are read-only over REST)
- 429 responses mean rate limiting; retried with exponential backoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any

import httpx

from archflow.config import Settings
from archflow.horizzon.auth import HorizzonAuth
from archflow.horizzon.errors import HorizzonAuthError, HorizzonError

__all__ = ["HorizzonClient", "HorizzonError", "HorizzonAuthError"]

#: Bulk endpoints are chunked to stay well below documented limits
#: (5000 objects per container, 500 links per entity).
_BULK_CHUNK = 500

#: Backoff schedule (seconds) for 429 responses.
_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)


class HorizzonClient:
    """Thin, typed wrapper around the Bizzdesign Open API.

    ``transport`` exists so tests can inject :class:`httpx.MockTransport`;
    ``sleep`` so tests can skip real backoff waits.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._auth = HorizzonAuth(settings)
        self._sleep = sleep
        base = settings.horizzon_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{base}/api/3.0",
            transport=transport,
            timeout=30.0,
        )

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HorizzonClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- plumbing --------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One API call with bearer auth, 401-refresh-once and 429 backoff."""
        refreshed = False
        attempt = 0
        while True:
            token = self._auth.get_token(self._client)
            response = self._client.request(
                method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )

            if response.status_code == 401 and not refreshed:
                self._auth.invalidate()
                refreshed = True
                continue

            if response.status_code == 429 and attempt < len(_RETRY_DELAYS):
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else _RETRY_DELAYS[attempt]
                except ValueError:
                    delay = _RETRY_DELAYS[attempt]
                self._sleep(delay)
                attempt += 1
                continue

            if response.status_code >= 400:
                raise HorizzonError(
                    f"{method} {path} failed with HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            return response

    def _paginate(
        self, path: str, params: dict[str, Any], page_size: int
    ) -> Iterator[dict[str, Any]]:
        """Iterate a paginated list endpoint (``{_items, _offset, _limit}``)."""
        offset = 0
        while True:
            page = self._request(
                "GET", path, params={**params, "offset": offset, "limit": page_size}
            ).json()
            items = page.get("_items", [])
            yield from items
            if len(items) < page_size:
                return
            offset += page_size

    # -- reads -----------------------------------------------------------------

    def list_repositories(self) -> list[dict[str, Any]]:
        """All model-package repositories the API client can access."""
        payload = self._request("GET", "/repositories").json()
        if isinstance(payload, dict):
            return list(payload.get("_items", []))
        return list(payload)

    def iter_objects(
        self,
        repository_id: int,
        *,
        type: str | None = None,  # noqa: A002 - mirrors the API's query parameter
        model_id: str | None = None,
        updated_after: str | None = None,
        include_external_ids: bool = True,
        page_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Stream objects from a repository, following pagination."""
        params: dict[str, Any] = {"includeExternalIds": include_external_ids}
        if type:
            params["type"] = type
        if model_id:
            params["modelId"] = model_id
        if updated_after:
            params["updatedAfter"] = updated_after
        yield from self._paginate(f"/repositories/{repository_id}/objects", params, page_size)

    def get_object(self, repository_id: int, object_id: str) -> dict[str, Any]:
        """One object by UUID."""
        return self._request(
            "GET", f"/repositories/{repository_id}/objects/{object_id}"
        ).json()

    def iter_relations(
        self,
        repository_id: int,
        *,
        relation_type: str | None = None,
        from_id: str | None = None,
        to_id: str | None = None,
        page_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Stream relations from a repository, following pagination."""
        params: dict[str, Any] = {}
        if relation_type:
            params["relationType"] = relation_type
        if from_id:
            params["fromId"] = from_id
        if to_id:
            params["toId"] = to_id
        yield from self._paginate(f"/repositories/{repository_id}/relations", params, page_size)

    # -- writes (architecture automation external data) -------------------------

    def list_collections(self, repository_id: int) -> list[dict[str, Any]]:
        """Externally-managed collections in the model package."""
        payload = self._request("GET", f"/repositories/{repository_id}/collections").json()
        if isinstance(payload, dict):
            return list(payload.get("_items", []))
        return list(payload)

    def create_collection(
        self, repository_id: int, name: str, *, external_id: str | None = None
    ) -> dict[str, Any]:
        """Create an externally-managed collection in the model package."""
        body: dict[str, Any] = {"name": {"en": name}}
        if external_id:
            body["externalId"] = external_id
        return self._request(
            "POST", f"/repositories/{repository_id}/collections", json=body
        ).json()

    def delete_collection(self, repository_id: int, collection_id: str) -> None:
        """Delete an externally-managed collection (and its contents)."""
        self._request(
            "DELETE", f"/repositories/{repository_id}/collections/{collection_id}"
        )

    def _bulk_post(self, path: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """POST items to a bulk endpoint in chunks, normalizing the response."""
        created: list[dict[str, Any]] = []
        for start in range(0, len(items), _BULK_CHUNK):
            chunk = items[start : start + _BULK_CHUNK]
            payload = self._request("POST", path, json=chunk).json()
            if isinstance(payload, list):
                created.extend(payload)
            elif isinstance(payload, dict):
                items_field = payload.get("_items")
                created.extend(items_field if isinstance(items_field, list) else [payload])
        return created

    def bulk_create_entities(
        self, repository_id: int, collection_id: str, entities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create entities in a collection, chunking to respect API limits."""
        return self._bulk_post(
            f"/repositories/{repository_id}/collections/{collection_id}/entities/bulk", entities
        )

    def bulk_create_links(
        self, repository_id: int, collection_id: str, links: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create links in a collection, chunking to respect API limits."""
        return self._bulk_post(
            f"/repositories/{repository_id}/collections/{collection_id}/links/bulk", links
        )
