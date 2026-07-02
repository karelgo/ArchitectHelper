"""FastAPI application for driving the governance process over REST.

``create_app()`` is a factory so tests can inject an engine wired to a
temporary database and stub actions:

    uvicorn "archflow.api.app:create_app" --factory
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

import archflow
from archflow.api.schemas import (
    AdvanceIn,
    CompleteIn,
    DecisionIn,
    HealthOut,
    RejectIn,
    RequestCreate,
    ReviewIn,
    TriageIn,
)
from archflow.config import get_settings
from archflow.domain.events import Event
from archflow.domain.models import ArchitectureRequest, Stakeholder
from archflow.storage import EventLog, RequestRepository, create_db_engine
from archflow.workflow.engine import AdvanceResult, WorkflowEngine


def default_engine() -> WorkflowEngine:
    """Engine wired to the configured database (used outside tests)."""
    settings = get_settings()
    db = create_db_engine(settings.database_url)
    return WorkflowEngine(
        repository=RequestRepository(db),
        events=EventLog(db),
        settings=settings,
    )


def create_app(engine: WorkflowEngine | None = None) -> FastAPI:
    """Build the ArchFlow REST API around a workflow engine."""
    app = FastAPI(
        title="ArchFlow API",
        version=archflow.__version__,
        description=(
            "Automation for the enterprise architecture governance process: "
            "intake, triage, stakeholder analysis, drafting, peer review, "
            "board approval and publication to BiZZdesign Horizzon."
        ),
    )
    wf = engine or default_engine()

    def load(request_id: str) -> ArchitectureRequest:
        request = wf.get(request_id)
        if request is None:
            raise HTTPException(status_code=404, detail=f"Unknown request id: {request_id}")
        return request

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
        return load(request_id)

    @app.post(
        "/requests/{request_id}/advance",
        response_model=AdvanceResult,
        tags=["workflow"],
        summary="Try to advance the request to the next stage",
        description=(
            "Runs the current stage's gate. Either the request moves on (and "
            "on-enter automation generates artifacts) or the response lists "
            "the reasons it is blocked."
        ),
    )
    def advance(request_id: str, payload: AdvanceIn | None = None) -> AdvanceResult:
        load(request_id)
        return wf.advance(request_id, actor=(payload.actor if payload else "api"))

    @app.post(
        "/requests/{request_id}/triage",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record the triage outcome (classification + impacted domains)",
    )
    def triage(request_id: str, payload: TriageIn) -> ArchitectureRequest:
        load(request_id)
        return wf.set_triage(
            request_id,
            classification=payload.classification,
            impacted_domains=payload.impacted_domains or None,
        )

    @app.post(
        "/requests/{request_id}/reviews",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record a peer-review verdict",
    )
    def record_review(request_id: str, payload: ReviewIn) -> ArchitectureRequest:
        load(request_id)
        return wf.record_review(
            request_id,
            reviewer=payload.reviewer,
            verdict=payload.verdict,
            comments=payload.comments,
        )

    @app.post(
        "/requests/{request_id}/decisions",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Record an architecture decision",
    )
    def record_decision(request_id: str, payload: DecisionIn) -> ArchitectureRequest:
        load(request_id)
        return wf.record_decision(
            request_id,
            title=payload.title,
            rationale=payload.rationale,
            status=payload.status,
            decided_by=payload.decided_by,
        )

    @app.post(
        "/requests/{request_id}/checklist/{key}/complete",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Manually complete a checklist item",
    )
    def complete_item(
        request_id: str, key: str, payload: CompleteIn | None = None
    ) -> ArchitectureRequest:
        load(request_id)
        try:
            return wf.complete_item(request_id, key, actor=(payload.actor if payload else "api"))
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @app.post(
        "/requests/{request_id}/reject",
        response_model=ArchitectureRequest,
        tags=["workflow"],
        summary="Reject the request (terminal)",
    )
    def reject(request_id: str, payload: RejectIn) -> ArchitectureRequest:
        load(request_id)
        return wf.reject(request_id, actor=payload.actor, reason=payload.reason)

    @app.get(
        "/requests/{request_id}/events",
        response_model=list[Event],
        tags=["requests"],
        summary="Audit trail for one request, oldest first",
    )
    def events(request_id: str) -> list[Event]:
        load(request_id)
        return wf.events_for(request_id)

    return app
