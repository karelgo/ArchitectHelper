"""Publish a request's stakeholder map to BiZZdesign Horizzon.

Two channels, used together:

1. **Open Exchange file** — always written. The Open API cannot carry views,
   so the diagram travels as a standard ArchiMate exchange file that an
   architect imports into Enterprise Studio.
2. **Open API push** — when Horizzon is configured, elements and
   relationships are additionally pushed as an *architecture automation*
   collection (entities + links), which appears in the model package's
   "Collections" folder in Enterprise Studio and on Horizzon sites without a
   publish step.

If the API push fails for any reason, publication degrades gracefully to the
file export (the result says so in ``detail``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from archflow.config import Settings
from archflow.domain.models import ArchitectureRequest
from archflow.horizzon.client import HorizzonClient, HorizzonError

#: Default mapping from our ArchiMate element types to Open API type terms.
#: Exact terms are tenant-metamodel dependent — see "Finding element type
#: names" in the Bizzdesign help. Override via ``HorizzonPublisher(element_terms=...)``.
ELEMENT_TERMS: dict[str, str] = {
    "Stakeholder": "ArchiMate:Stakeholder",
    "Driver": "ArchiMate:Driver",
    "Assessment": "ArchiMate:Assessment",
    "Goal": "ArchiMate:Goal",
}

#: Default mapping from relationship types to Open API link type terms
#: (best-effort; verify against your tenant's metamodel).
RELATION_TERMS: dict[str, str] = {
    "Association": "ArchiMate:AssociationRelation",
    "Influence": "ArchiMate:InfluenceRelation",
}


class PublishResult(BaseModel):
    """Outcome of a publication attempt."""

    success: bool
    mode: Literal["api", "file_export"]
    detail: str
    artifact_path: str | None = None


class HorizzonPublisher:
    """Publishes stakeholder maps to Horizzon, with file-export fallback."""

    def __init__(
        self,
        settings: Settings,
        client: HorizzonClient | None = None,
        element_terms: dict[str, str] | None = None,
        relation_terms: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._element_terms = element_terms or ELEMENT_TERMS
        self._relation_terms = relation_terms or RELATION_TERMS

    # -- helpers ---------------------------------------------------------------

    def _export_file(self, request: ArchitectureRequest, xml: str) -> Path:
        directory = self._settings.artifacts_dir / request.id
        directory.mkdir(parents=True, exist_ok=True)
        # Distinct from the stakeholder-analysis artifact
        # (stakeholder_map.archimate.xml): the reviewed map must stay
        # immutable; this file is the publication-time snapshot.
        path = directory / "publication_export.archimate.xml"
        path.write_text(xml, encoding="utf-8")
        return path

    def _external_id(self, request: ArchitectureRequest, suffix: str) -> str:
        return f"archflow-{request.id}-{suffix}"

    def _push_to_api(
        self, request: ArchitectureRequest, model: Any, repository_id: int | None
    ) -> str:
        """Push elements/relationships as an architecture-automation collection.

        Returns a human-readable summary; raises :class:`HorizzonError` on
        any API failure.
        """
        client = self._client or HorizzonClient(self._settings)
        owns_client = self._client is None
        try:
            if repository_id is None:
                repositories = client.list_repositories()
                if not repositories:
                    raise HorizzonError("No repositories accessible to this API client")
                repository_id = int(repositories[0]["id"])

            collection = client.create_collection(
                repository_id,
                f"ArchFlow — {request.title}",
                external_id=f"archflow-{request.id}",
            )
            collection_id = str(collection.get("id", collection.get("externalId", "")))

            id_to_external: dict[str, str] = {}
            entities: list[dict[str, Any]] = []
            for element in model.elements:
                term = self._element_terms.get(element.type)
                if term is None:
                    continue
                external_id = self._external_id(request, element.id)
                id_to_external[element.id] = external_id
                entities.append(
                    {"externalId": external_id, "type": term, "name": {"en": element.name}}
                )
            client.bulk_create_entities(repository_id, collection_id, entities)

            links: list[dict[str, Any]] = []
            for rel in model.relationships:
                term = self._relation_terms.get(rel.type)
                source = id_to_external.get(rel.source)
                target = id_to_external.get(rel.target)
                if term is None or source is None or target is None:
                    continue
                links.append(
                    {
                        "externalId": self._external_id(request, rel.id),
                        "type": term,
                        "fromExternalId": source,
                        "toExternalId": target,
                    }
                )
            client.bulk_create_links(repository_id, collection_id, links)
            return (
                f"Pushed {len(entities)} entities and {len(links)} links to "
                f"repository {repository_id} as collection 'ArchFlow — {request.title}'"
            )
        finally:
            if owns_client:
                client.close()

    # -- public ------------------------------------------------------------------

    def publish(
        self, request: ArchitectureRequest, repository_id: int | None = None
    ) -> PublishResult:
        """Publish the request's stakeholder map (API push + file export)."""
        from archflow.archimate.stakeholder_map import build_stakeholder_map

        model = build_stakeholder_map(request)
        path = self._export_file(request, model.to_open_exchange_xml())

        if not self._settings.horizzon_configured:
            return PublishResult(
                success=True,
                mode="file_export",
                detail=(
                    "Horizzon is not configured (see .env.example). Import the "
                    "Open Exchange file into Enterprise Studio manually: "
                    f"{path}"
                ),
                artifact_path=str(path),
            )

        try:
            summary = self._push_to_api(request, model, repository_id)
        except HorizzonError as err:
            return PublishResult(
                success=True,
                mode="file_export",
                detail=(
                    f"API publish failed ({err}); Open Exchange export written "
                    "instead — import it into Enterprise Studio manually."
                ),
                artifact_path=str(path),
            )

        return PublishResult(
            success=True,
            mode="api",
            detail=f"{summary}. Views travel by file: import {path} into Enterprise Studio.",
            artifact_path=str(path),
        )
