"""FastAPI application for driving the governance process over REST.

``create_app()`` is a factory so tests can inject an engine wired to a
temporary database and stub actions:

    uvicorn "archflow.api.app:create_app" --factory
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import archflow
from archflow.api.assistant import build_assistant_router
from archflow.api.schemas import (
    AdvanceIn,
    CompleteIn,
    DecisionIn,
    HealthOut,
    RejectIn,
    RequestCreate,
    ReviewIn,
    StakeholderIn,
    TriageIn,
)
from archflow.api.studio import build_studio_router
from archflow.assistant.copilot import ArchiMateCopilot
from archflow.assistant.governance import GovernanceAssistant
from archflow.config import get_settings
from archflow.domain.events import Event
from archflow.domain.models import ArchitectureRequest, Stakeholder
from archflow.storage import ViewProjectRepository, create_db_engine
from archflow.studio.service import StudioService
from archflow.workflow.engine import (
    AdvanceResult,
    GuardViolation,
    WorkflowEngine,
    build_default_engine,
)

T = TypeVar("T")


class UiConfig(BaseModel):
    drawio_embed_url: str
    assistant_available: bool
    version: str


def create_app(
    engine: WorkflowEngine | None = None,
    studio: StudioService | None = None,
    copilot_factory: Callable[[], ArchiMateCopilot] | None = None,
    assistant_factory: Callable[[], GovernanceAssistant] | None = None,
) -> FastAPI:
    """Build the ArchFlow REST API + Studio + web UI around a workflow engine."""
    app = FastAPI(
        title="ArchFlow API",
        version=archflow.__version__,
        description=(
            "Automation for the enterprise architecture governance process: "
            "intake, triage, stakeholder analysis, drafting, peer review, "
            "board approval and publication to BiZZdesign Horizzon."
        ),
    )
    settings = get_settings()
    wf = engine or build_default_engine(settings)
    if studio is None:
        studio = StudioService(ViewProjectRepository(create_db_engine(settings.database_url)))

    def run(fn: Callable[[], T]) -> T:
        """Translate engine errors into HTTP errors (one load per call).

        Only *expected* errors map to 4xx: unknown ids (404) and guard
        refusals (409). Anything else — including internal ValueErrors from
        model generation — propagates as a 500 so defects stay visible.
        """
        try:
            return fn()
        except KeyError as err:
            detail = err.args[0] if err.args else str(err)
            raise HTTPException(status_code=404, detail=detail) from err
        except GuardViolation as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    @app.get("/health", response_model=HealthOut, tags=["meta"], summary="Liveness probe")
    def health() -> HealthOut:
        return HealthOut(status="ok", version=archflow.__version__)

    @app.post(
        "/requests",
        response_model=ArchitectureRequest,
        status_code=201,
        tags=["requests"],
        summary="Register a new architecture request (intake)",
    )
    def create_request(payload: RequestCreate) -> ArchitectureRequest:
        return wf.create_request(
            title=payload.title,
            description=payload.description,
            requester=payload.requester,
            business_goal=payload.business_goal,
            impacted_domains=payload.impacted_domains,
            stakeholders=[Stakeholder(**s.model_dump()) for s in payload.stakeholders],
        )

    @app.get(
        "/requests",
        response_model=list[ArchitectureRequest],
        tags=["requests"],
        summary="List requests, most recently updated first",
    )
    def list_requests() -> list[ArchitectureRequest]:
        return wf.list_requests()

    @app.get(
        "/requests/{request_id}",
        response_model=ArchitectureRequest,
        tags=["requests"],
        summary="Fetch one request",
    )
    def get_request(request_id: str) -> ArchitectureRequest:
        return run(lambda: wf.load(request_id))

    @app.post(
        "/requests/{request_id}/advance",
        response_model=AdvanceResult,
        tags=["workflow"],
        summary="Try to advance the request to the next stage",
        description=(
            "Runs the current stage's gate. Either the request moves on (and "
            "automation refreshes/generates artifacts) or the response lists "
            "the reasons it is blocked."
        ),
    )
    def advance(request_id: str, payload: AdvanceIn | None = None) -> AdvanceResult:
        return run(lambda: wf.advance(request_id, actor=(payload.actor if payload else "api")))

    @app.post(
        "/requests/{request_id}/triage",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record the triage outcome (classification + impacted domains)",
    )
    def triage(request_id: str, payload: TriageIn) -> ArchitectureRequest:
        return run(
            lambda: wf.set_triage(
                request_id,
                classification=payload.classification,
                impacted_domains=payload.impacted_domains or None,
            )
        )

    @app.post(
        "/requests/{request_id}/stakeholders",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Add a stakeholder (typically during stakeholder analysis)",
    )
    def add_stakeholder(request_id: str, payload: StakeholderIn) -> ArchitectureRequest:
        return run(
            lambda: wf.add_stakeholder(request_id, Stakeholder(**payload.model_dump()))
        )

    @app.post(
        "/requests/{request_id}/reviews",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record a peer-review verdict",
    )
    def record_review(request_id: str, payload: ReviewIn) -> ArchitectureRequest:
        return run(
            lambda: wf.record_review(
                request_id,
                reviewer=payload.reviewer,
                verdict=payload.verdict,
                comments=payload.comments,
            )
        )

    @app.post(
        "/requests/{request_id}/decisions",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record an architecture decision",
    )
    def record_decision(request_id: str, payload: DecisionIn) -> ArchitectureRequest:
        return run(
            lambda: wf.record_decision(
                request_id,
                title=payload.title,
                rationale=payload.rationale,
                status=payload.status,
                decided_by=payload.decided_by,
            )
        )

    @app.post(
        "/requests/{request_id}/checklist/{key}/complete",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Manually complete a custom checklist item",
        description=(
            "Only custom items can be completed by hand: auto-conditioned "
            "items complete themselves (409), unknown keys are 404."
        ),
    )
    def complete_item(
        request_id: str, key: str, payload: CompleteIn | None = None
    ) -> ArchitectureRequest:
        return run(
            lambda: wf.complete_item(request_id, key, actor=(payload.actor if payload else "api"))
        )

    @app.post(
        "/requests/{request_id}/reject",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Reject the request (terminal)",
    )
    def reject(request_id: str, payload: RejectIn) -> ArchitectureRequest:
        return run(lambda: wf.reject(request_id, actor=payload.actor, reason=payload.reason))

    @app.get(
        "/requests/{request_id}/events",
        response_model=list[Event],
        tags=["requests"],
        summary="Audit trail for one request, oldest first",
    )
    def events(request_id: str) -> list[Event]:
        return run(lambda: wf.events_for(request_id))

    @app.get("/ui-config", response_model=UiConfig, tags=["meta"], summary="Web UI settings")
    def ui_config() -> UiConfig:
        return UiConfig(
            drawio_embed_url=settings.drawio_embed_url,
            assistant_available=settings.assistant_configured or copilot_factory is not None,
            version=archflow.__version__,
        )

    app.include_router(build_studio_router(studio, wf, settings, copilot_factory))
    app.include_router(build_assistant_router(wf, settings, assistant_factory))

    static_dir = resources.files("archflow.ui") / "static"
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    return app
