"""Request/response schemas for the REST API.

Domain models (:class:`~archflow.domain.models.ArchitectureRequest`,
:class:`~archflow.workflow.engine.AdvanceResult`, events) are returned
directly as responses; these schemas cover inbound payloads.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archflow.domain.models import (
    Attitude,
    Classification,
    DecisionStatus,
    InfluenceLevel,
    ReviewVerdict,
)


class StakeholderIn(BaseModel):
    name: str
    role: str = ""
    concerns: list[str] = Field(default_factory=list)
    influence: InfluenceLevel = InfluenceLevel.MEDIUM
    interest: InfluenceLevel = InfluenceLevel.MEDIUM
    attitude: Attitude = Attitude.NEUTRAL


class RequestCreate(BaseModel):
    title: str
    description: str = ""
    requester: str = ""
    business_goal: str = ""
    impacted_domains: list[str] = Field(default_factory=list)
    stakeholders: list[StakeholderIn] = Field(default_factory=list)


class TriageIn(BaseModel):
    classification: Classification
    impacted_domains: list[str] = Field(default_factory=list)


class ReviewIn(BaseModel):
    reviewer: str
    verdict: ReviewVerdict
    comments: str = ""


class DecisionIn(BaseModel):
    title: str
    rationale: str = ""
    status: DecisionStatus = DecisionStatus.PROPOSED
    decided_by: str = ""


class RejectIn(BaseModel):
    actor: str
    reason: str


class CompleteIn(BaseModel):
    actor: str = "api"


class AdvanceIn(BaseModel):
    actor: str = "api"


class HealthOut(BaseModel):
    status: str
    version: str
