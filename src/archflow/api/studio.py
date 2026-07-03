"""REST routes for the Studio: view projects, draw.io round-trip, copilot."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from archflow.archimate.lint import LintFinding, lint_model
from archflow.archimate.svg import view_to_svg
from archflow.assistant.copilot import ArchiMateCopilot, CopilotReply, CopilotUnavailable
from archflow.config import Settings
from archflow.studio.models import ViewProject
from archflow.studio.service import StudioService
from archflow.workflow.engine import WorkflowEngine

T = TypeVar("T")


class ViewProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    request_id: str | None
    elements: int
    relationships: int
    updated_at: str


class ViewProjectCreate(BaseModel):
    name: str
    description: str = ""
    request_id: str | None = None  # seed from this request's stakeholder map


class ViewProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class DrawioDocument(BaseModel):
    xml: str


class DrawioSaved(BaseModel):
    synced_nodes: int


class ExchangeImport(BaseModel):
    name: str = ""
    xml: str


class CopilotMessage(BaseModel):
    message: str = Field(min_length=1)


def _summary(project: ViewProject) -> ViewProjectSummary:
    return ViewProjectSummary(
        id=project.id,
        name=project.name,
        description=project.description,
        request_id=project.request_id,
        elements=len(project.model.elements),
        relationships=len(project.model.relationships),
        updated_at=project.updated_at.isoformat(),
    )


def build_studio_router(
    studio: StudioService,
    engine: WorkflowEngine,
    settings: Settings,
    copilot_factory: Callable[[], ArchiMateCopilot] | None = None,
) -> APIRouter:
    """Studio API around a service instance (test seam: inject a copilot factory)."""
    router = APIRouter(prefix="/studio", tags=["studio"])

    def run(fn: Callable[[], T]) -> T:
        try:
            return fn()
        except KeyError as err:
            detail = err.args[0] if err.args else str(err)
            raise HTTPException(status_code=404, detail=detail) from err

    def make_copilot() -> ArchiMateCopilot:
        if copilot_factory is not None:
            return copilot_factory()
        return ArchiMateCopilot(settings)

    @router.get("/views", response_model=list[ViewProjectSummary], summary="List view projects")
    def list_views() -> list[ViewProjectSummary]:
        return [_summary(p) for p in studio.list()]

    @router.post(
        "/views",
        response_model=ViewProject,
        status_code=201,
        summary="Create a view project (blank, or seeded from a request's stakeholder map)",
    )
    def create_view(payload: ViewProjectCreate) -> ViewProject:
        if payload.request_id:
            request = run(lambda: engine.load(payload.request_id or ""))
            return studio.create_from_request(payload.name, request)
        return studio.create(payload.name, payload.description)

    @router.get("/views/{project_id}", response_model=ViewProject, summary="Fetch one project")
    def get_view(project_id: str) -> ViewProject:
        return run(lambda: studio.get(project_id))

    @router.patch(
        "/views/{project_id}", response_model=ViewProject, summary="Rename / re-describe"
    )
    def patch_view(project_id: str, payload: ViewProjectPatch) -> ViewProject:
        return run(lambda: studio.update_meta(project_id, payload.name, payload.description))

    @router.delete("/views/{project_id}", status_code=204, summary="Delete a project")
    def delete_view(project_id: str) -> Response:
        studio.delete(project_id)
        return Response(status_code=204)

    @router.get(
        "/views/{project_id}/drawio",
        response_model=DrawioDocument,
        summary="The draw.io document for the embedded editor",
    )
    def get_drawio(project_id: str) -> DrawioDocument:
        return DrawioDocument(xml=run(lambda: studio.drawio_document(project_id)))

    @router.put(
        "/views/{project_id}/drawio",
        response_model=DrawioSaved,
        summary="Save the edited draw.io document (geometry syncs into the model)",
    )
    def put_drawio(project_id: str, payload: DrawioDocument) -> DrawioSaved:
        try:
            synced = run(lambda: studio.save_drawio_document(project_id, payload.xml))
        except Exception as err:  # noqa: BLE001 - malformed XML from the editor
            if isinstance(err, HTTPException):
                raise
            raise HTTPException(status_code=422, detail=f"Invalid draw.io XML: {err}") from err
        return DrawioSaved(synced_nodes=synced)

    @router.get(
        "/views/{project_id}/exchange",
        summary="Download the ArchiMate Open Exchange file",
        response_class=Response,
    )
    def get_exchange(project_id: str) -> Response:
        xml = run(lambda: studio.exchange_document(project_id))
        project = run(lambda: studio.get(project_id))
        # HTTP headers are latin-1: keep the filename ASCII-safe.
        stem = (project.name or "model").encode("ascii", "replace").decode("ascii")
        filename = f"{stem}.archimate.xml".replace("/", "_").replace('"', "_")
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get(
        "/views/{project_id}/lint",
        response_model=list[LintFinding],
        summary="Lint the project's ArchiMate model",
        description=(
            "Semantic checks (illegal relationship endpoints, unrealizable "
            "targets, cross-layer structure) and structural checks (dangling "
            "references, duplicates, orphans), most severe first."
        ),
    )
    def lint(project_id: str) -> list[LintFinding]:
        project = run(lambda: studio.get(project_id))
        return lint_model(project.model)

    @router.get(
        "/views/{project_id}/preview.svg",
        summary="A lightweight SVG rendering of the project's first view",
        response_class=Response,
    )
    def preview(project_id: str) -> Response:
        project = run(lambda: studio.get(project_id))
        return Response(
            content=view_to_svg(project.model),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post(
        "/views/import",
        response_model=ViewProject,
        status_code=201,
        summary="Create a project from an ArchiMate Open Exchange file",
    )
    def import_exchange(payload: ExchangeImport) -> ViewProject:
        try:
            return studio.import_exchange(payload.xml, payload.name)
        except Exception as err:  # noqa: BLE001 - user-supplied XML
            raise HTTPException(status_code=422, detail=f"Invalid exchange XML: {err}") from err

    @router.post(
        "/views/{project_id}/assistant",
        response_model=CopilotReply,
        summary="Send a message to the ArchiMate copilot",
        description=(
            "The copilot builds the view via tools; when it changes the model, "
            "reload the draw.io document. 503 when no Anthropic API key is set."
        ),
    )
    def assistant(project_id: str, payload: CopilotMessage) -> CopilotReply:
        project = run(lambda: studio.get(project_id))
        try:
            copilot = make_copilot()
        except CopilotUnavailable as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

        reply = copilot.chat(project, payload.message, context=request_context(project))
        studio.save(project)
        return reply

    def request_context(project: ViewProject) -> str | None:
        """The linked governance request as prompt context (None when absent)."""
        if not project.request_id:
            return None
        try:
            from archflow.assistant.governance import request_brief

            return request_brief(engine.load(project.request_id))
        except KeyError:
            return None  # request was deleted; the view stands alone

    @router.post(
        "/views/{project_id}/assistant/stream",
        summary="Copilot chat as server-sent events (per-round progress)",
        description=(
            "Emits `round` events while the copilot works (each with that "
            "round's actions) and one `final` event with the reply. The "
            "project saves when the stream ends."
        ),
    )
    def assistant_stream(project_id: str, payload: CopilotMessage) -> StreamingResponse:
        project = run(lambda: studio.get(project_id))
        try:
            copilot = make_copilot()
        except CopilotUnavailable as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        context = request_context(project)

        def events() -> Iterator[str]:
            try:
                for event in copilot.chat_stream(project, payload.message, context=context):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as err:  # noqa: BLE001 - surfaced to the client, not hidden
                yield f"data: {json.dumps({'type': 'error', 'message': str(err)})}\n\n"
            finally:
                studio.save(project)  # persist whatever the loop completed

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
