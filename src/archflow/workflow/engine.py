"""The workflow engine: moves architecture requests through the pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from archflow.config import Settings, get_settings
from archflow.domain.events import Event, EventType
from archflow.domain.models import (
    PIPELINE,
    ArchitectureRequest,
    Artifact,
    Classification,
    Decision,
    DecisionStatus,
    Review,
    ReviewVerdict,
    Stage,
    Stakeholder,
    next_stage,
    utcnow,
)
from archflow.storage.repository import EventLog, RequestRepository
from archflow.workflow.actions import ActionRegistry, AutomationContext, default_registry
from archflow.workflow.stages import AUTO_KEYS, auto_complete, checklist_for, gate_for


class GuardViolation(ValueError):
    """A governance guard refused the operation (HTTP 409 at the API layer).

    Distinct from plain ``ValueError`` so internal defects don't masquerade
    as business-rule conflicts.
    """


def build_default_engine(settings: Settings | None = None) -> WorkflowEngine:
    """Engine wired to the configured database — the one construction path
    shared by the CLI and the REST API."""
    settings = settings or get_settings()
    from archflow.storage.db import create_db_engine

    db = create_db_engine(settings.database_url)
    return WorkflowEngine(
        repository=RequestRepository(db), events=EventLog(db), settings=settings
    )


class AdvanceResult(BaseModel):
    """Outcome of one attempt to advance a request to the next stage."""

    advanced: bool
    from_stage: Stage
    to_stage: Stage | None = None
    reasons: list[str] = Field(default_factory=list)
    generated: list[Artifact] = Field(default_factory=list)


class WorkflowEngine:
    """Drives requests through the governance pipeline with gates and actions."""

    def __init__(
        self,
        repository: RequestRepository,
        events: EventLog,
        actions: ActionRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._repo = repository
        self._events = events
        self._actions = actions or default_registry()
        self._settings = settings or get_settings()

    # -- helpers -----------------------------------------------------------

    def _emit(
        self,
        request_id: str,
        type_: EventType,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            Event(request_id=request_id, type=type_, actor=actor, payload=payload or {})
        )

    def _load(self, request_id: str) -> ArchitectureRequest:
        request = self._repo.get(request_id)
        if request is None:
            raise KeyError(f"Unknown request id: {request_id!r}")
        return request

    # -- lifecycle ----------------------------------------------------------

    def create_request(
        self,
        title: str,
        description: str = "",
        requester: str = "",
        business_goal: str = "",
        impacted_domains: list[str] | None = None,
        stakeholders: list[Stakeholder] | None = None,
    ) -> ArchitectureRequest:
        """Create a new request at intake, with the full pipeline checklist seeded."""
        request = ArchitectureRequest(
            title=title,
            description=description,
            requester=requester,
            business_goal=business_goal,
            impacted_domains=impacted_domains or [],
            stakeholders=stakeholders or [],
        )
        for stage in PIPELINE:
            request.checklist.extend(checklist_for(stage))
        auto_complete(request)
        self._repo.save(request)
        self._emit(request.id, EventType.REQUEST_CREATED, payload={"title": title})
        self._emit(
            request.id, EventType.STAGE_ENTERED, payload={"stage": Stage.INTAKE.value}
        )
        return request

    def advance(self, request_id: str, actor: str = "system") -> AdvanceResult:
        """Try to move the request to the next stage, running gate and actions."""
        request = self._load(request_id)
        from_stage = request.stage

        if from_stage in (Stage.DONE, Stage.REJECTED):
            return AdvanceResult(
                advanced=False,
                from_stage=from_stage,
                reasons=[f"Request is in terminal stage '{from_stage.value}'"],
            )

        auto_completed = bool(auto_complete(request))

        ok, reasons = gate_for(request)
        if not ok:
            if auto_completed:  # persist auto-completions; success path saves later
                request.touch()
                self._repo.save(request)
            self._emit(
                request_id,
                EventType.STAGE_BLOCKED,
                actor=actor,
                payload={"stage": from_stage.value, "reasons": reasons},
            )
            return AdvanceResult(advanced=False, from_stage=from_stage, reasons=reasons)

        to_stage = next_stage(request)
        if to_stage is None:  # defensive: non-terminal stages always have a successor
            return AdvanceResult(
                advanced=False, from_stage=from_stage, reasons=["No next stage available"]
            )

        ctx = AutomationContext(
            request=request, settings=self._settings, artifacts_dir=self._settings.artifacts_dir
        )
        # Refresh the leaving stage's artifacts first, so the recorded
        # deliverables include everything added during the stage.
        generated = self._actions.on_exit(from_stage, ctx)

        request.stage = to_stage
        request.touch()

        generated += self._actions.on_enter(to_stage, ctx)
        for artifact in generated:
            # A regenerated deliverable replaces its previous record: one
            # artifact entry per path, pointing at the current content.
            request.artifacts = [a for a in request.artifacts if a.path != artifact.path]
            request.artifacts.append(artifact)
            self._emit(
                request_id,
                EventType.ARTIFACT_GENERATED,
                actor=actor,
                payload={
                    "artifact_id": artifact.id,
                    "kind": artifact.kind.value,
                    "name": artifact.name,
                    "path": artifact.path,
                },
            )

        auto_complete(request)
        self._repo.save(request)
        self._emit(
            request_id,
            EventType.STAGE_ENTERED,
            actor=actor,
            payload={"stage": to_stage.value, "from": from_stage.value},
        )
        return AdvanceResult(
            advanced=True, from_stage=from_stage, to_stage=to_stage, generated=generated
        )

    def reject(self, request_id: str, actor: str, reason: str) -> ArchitectureRequest:
        """Reject the request (terminal side-exit)."""
        request = self._load(request_id)
        request.stage = Stage.REJECTED
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id, EventType.REQUEST_REJECTED, actor=actor, payload={"reason": reason}
        )
        return request

    # -- recording ----------------------------------------------------------

    def complete_item(self, request_id: str, key: str, actor: str) -> ArchitectureRequest:
        """Manually mark one checklist item done.

        Only custom items can be completed by hand: items with an objective
        auto-condition complete themselves when the condition is met, and
        items of other stages are out of reach — both guards exist so the
        stage gates cannot be bypassed.

        Raises:
            KeyError: If ``key`` is not on the request's checklist.
            ValueError: If the item is auto-completed or belongs to a
                different stage than the request is currently in.
        """
        request = self._load(request_id)
        item = next((i for i in request.checklist if i.key == key), None)
        if item is None:
            raise KeyError(f"Unknown checklist key: {key!r}")
        if key in AUTO_KEYS:
            raise GuardViolation(
                f"Checklist item {key!r} completes automatically when its condition "
                "is met; it cannot be completed by hand."
            )
        if item.stage != request.stage:
            raise GuardViolation(
                f"Checklist item {key!r} belongs to stage '{item.stage.value}'; "
                f"the request is in '{request.stage.value}'."
            )
        item.done = True
        item.completed_by = actor
        item.completed_at = utcnow()
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id, EventType.CHECKLIST_COMPLETED, actor=actor, payload={"key": key}
        )
        return request

    def record_review(
        self, request_id: str, reviewer: str, verdict: ReviewVerdict, comments: str = ""
    ) -> ArchitectureRequest:
        """Record a peer review verdict on the request."""
        request = self._load(request_id)
        review = Review(reviewer=reviewer, verdict=verdict, comments=comments, stage=request.stage)
        request.reviews.append(review)
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id,
            EventType.REVIEW_RECORDED,
            actor=reviewer,
            payload={"verdict": review.verdict.value, "stage": review.stage.value},
        )
        return request

    def record_decision(
        self,
        request_id: str,
        title: str,
        rationale: str = "",
        status: DecisionStatus = DecisionStatus.PROPOSED,
        decided_by: str = "",
    ) -> ArchitectureRequest:
        """Record an architecture decision on the request."""
        request = self._load(request_id)
        decision = Decision(
            title=title,
            rationale=rationale,
            status=status,
            decided_by=decided_by,
            decided_at=utcnow() if status != DecisionStatus.PROPOSED else None,
            stage=request.stage,
        )
        request.decisions.append(decision)
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id,
            EventType.DECISION_RECORDED,
            actor=decided_by or "system",
            payload={"title": title, "status": decision.status.value},
        )
        return request

    def add_stakeholder(self, request_id: str, stakeholder: Stakeholder) -> ArchitectureRequest:
        """Add a stakeholder to the request (up to and including analysis).

        Raises:
            GuardViolation: Once the request is past stakeholder analysis —
                later additions would silently diverge from the reviewed map.
        """
        request = self._load(request_id)
        allowed = (Stage.INTAKE, Stage.TRIAGE, Stage.STAKEHOLDER_ANALYSIS)
        if request.stage not in allowed:
            raise GuardViolation(
                "Stakeholders can only be added up to and including the "
                f"stakeholder-analysis stage; the request is in '{request.stage.value}'."
            )
        request.stakeholders.append(stakeholder)
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id,
            EventType.STAKEHOLDER_ADDED,
            payload={"name": stakeholder.name, "role": stakeholder.role},
        )
        return request

    def record_artifact(
        self, request_id: str, artifact: Artifact, actor: str = "system"
    ) -> ArchitectureRequest:
        """Attach an externally produced artifact to the audit trail.

        Used by out-of-band flows (e.g. a manual ``archflow publish``) so
        every deliverable is traceable; same path-replacement semantics as
        artifacts generated by stage automation.
        """
        request = self._load(request_id)
        request.artifacts = [a for a in request.artifacts if a.path != artifact.path]
        request.artifacts.append(artifact)
        auto_complete(request)
        request.touch()
        self._repo.save(request)
        self._emit(
            request_id,
            EventType.ARTIFACT_GENERATED,
            actor=actor,
            payload={
                "artifact_id": artifact.id,
                "kind": artifact.kind.value,
                "name": artifact.name,
                "path": artifact.path,
            },
        )
        return request

    def set_triage(
        self,
        request_id: str,
        classification: Classification,
        impacted_domains: list[str] | None = None,
    ) -> ArchitectureRequest:
        """Record the triage outcome: classification and impacted domains.

        Only allowed while the request is at intake or triage — reclassifying
        later (e.g. to *small* after peer review) would silently change which
        gates still apply.

        Raises:
            ValueError: If the request has already passed triage.
        """
        request = self._load(request_id)
        if request.stage not in (Stage.INTAKE, Stage.TRIAGE):
            raise GuardViolation(
                f"Triage can only be set at intake or triage; the request is in "
                f"'{request.stage.value}'."
            )
        request.classification = classification
        if impacted_domains:
            request.impacted_domains = impacted_domains
        auto_complete(request)
        request.touch()
        self._repo.save(request)
        return request

    # -- queries -------------------------------------------------------------

    def get(self, request_id: str) -> ArchitectureRequest | None:
        """Load one request by id."""
        return self._repo.get(request_id)

    def load(self, request_id: str) -> ArchitectureRequest:
        """Load one request by id, raising ``KeyError`` when unknown."""
        return self._load(request_id)

    def list_requests(self) -> list[ArchitectureRequest]:
        """All requests, most recently updated first."""
        return self._repo.list()

    def events_for(self, request_id: str) -> list[Event]:
        """Audit-trail events for one request, oldest first."""
        self._load(request_id)  # 404-style KeyError for unknown ids
        return self._events.for_request(request_id)
