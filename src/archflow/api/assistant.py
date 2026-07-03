"""REST routes for the governance assistants (AI drafts, human-applied)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from archflow.assistant.copilot import CopilotUnavailable
from archflow.assistant.governance import (
    GovernanceAssistant,
    PsaDraft,
    ReviewDraft,
    StakeholderProposal,
    existing_psa_text,
    proposal_to_domain,
    save_psa_draft,
)
from archflow.config import Settings
from archflow.workflow.engine import GuardViolation, WorkflowEngine

T = TypeVar("T")


class PsaIn(BaseModel):
    save: bool = False
    markdown: str = Field(
        "",
        description=(
            "A previously drafted PSA to save as-is (skips generation). "
            "Empty: generate a fresh draft."
        ),
    )


class ApplyProposalIn(BaseModel):
    proposal: StakeholderProposal
    actor: str = "ui"


class AppliedOut(BaseModel):
    stakeholders: int = Field(0, description="Newly added (after dedup)")
    drivers: int = 0
    goals: int = 0
    assessments: int = 0


def build_assistant_router(
    engine: WorkflowEngine,
    settings: Settings,
    assistant_factory: Callable[[], GovernanceAssistant] | None = None,
) -> APIRouter:
    """AI-draft endpoints per request (test seam: inject an assistant factory)."""
    router = APIRouter(prefix="/requests/{request_id}/assistant", tags=["assistant"])

    def run(fn: Callable[[], T]) -> T:
        try:
            return fn()
        except KeyError as err:
            detail = err.args[0] if err.args else str(err)
            raise HTTPException(status_code=404, detail=detail) from err
        except GuardViolation as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    def make_assistant() -> GovernanceAssistant:
        try:
            if assistant_factory is not None:
                return assistant_factory()
            return GovernanceAssistant(settings)
        except CopilotUnavailable as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    @router.post(
        "/stakeholders",
        response_model=StakeholderProposal,
        summary="Draft a stakeholder analysis (proposal only — apply separately)",
    )
    def draft_stakeholders(request_id: str) -> StakeholderProposal:
        request = run(lambda: engine.load(request_id))
        return make_assistant().draft_stakeholder_analysis(request)

    @router.post(
        "/stakeholders/apply",
        response_model=AppliedOut,
        summary="Apply a (human-approved) stakeholder-analysis proposal",
    )
    def apply_stakeholders(request_id: str, payload: ApplyProposalIn) -> AppliedOut:
        stakeholders, drivers, goals, assessments = proposal_to_domain(payload.proposal)
        before = run(lambda: engine.load(request_id))
        counts_before = (
            len(before.stakeholders),
            len(before.drivers),
            len(before.goals),
            len(before.assessments),
        )
        after = run(
            lambda: engine.apply_analysis(
                request_id,
                stakeholders=stakeholders,
                drivers=drivers,
                goals=goals,
                assessments=assessments,
                actor=payload.actor,
            )
        )
        return AppliedOut(
            stakeholders=len(after.stakeholders) - counts_before[0],
            drivers=len(after.drivers) - counts_before[1],
            goals=len(after.goals) - counts_before[2],
            assessments=len(after.assessments) - counts_before[3],
        )

    @router.post(
        "/psa",
        response_model=PsaDraft,
        summary="Draft the PSA prose (optionally save as the request's PSA artifact)",
    )
    def draft_psa(request_id: str, payload: PsaIn | None = None) -> PsaDraft:
        request = run(lambda: engine.load(request_id))
        if payload and payload.markdown.strip():
            markdown = payload.markdown  # human-reviewed draft from an earlier call
        else:
            generated = make_assistant().draft_psa(
                request, current_psa=existing_psa_text(request)
            )
            markdown = f"# Project Start Architecture: {request.title}\n\n{generated}"

        saved_path: str | None = None
        if payload and payload.save:
            saved_path = run(lambda: save_psa_draft(engine, settings, request, markdown))
        return PsaDraft(markdown=markdown, saved_path=saved_path)

    @router.post(
        "/review",
        response_model=ReviewDraft,
        summary="Draft a peer review (never recorded — a human records the verdict)",
    )
    def draft_review(request_id: str) -> ReviewDraft:
        request = run(lambda: engine.load(request_id))
        return make_assistant().pre_review(request, psa_text=existing_psa_text(request))

    return router
