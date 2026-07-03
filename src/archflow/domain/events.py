"""Audit-trail events emitted by the workflow engine.

Every state change on a request is recorded as an :class:`Event`, giving a
complete, replayable history of the governance process.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from archflow.domain.models import new_id, utcnow


class EventType(StrEnum):
    REQUEST_CREATED = "request_created"
    STAGE_ENTERED = "stage_entered"
    STAGE_BLOCKED = "stage_blocked"
    STAKEHOLDER_ADDED = "stakeholder_added"
    ANALYSIS_APPLIED = "analysis_applied"
    CHECKLIST_COMPLETED = "checklist_completed"
    ARTIFACT_GENERATED = "artifact_generated"
    REVIEW_RECORDED = "review_recorded"
    DECISION_RECORDED = "decision_recorded"
    OWNER_ASSIGNED = "owner_assigned"
    AI_DRAFT_SAVED = "ai_draft_saved"
    REQUEST_REJECTED = "request_rejected"
    PUBLISHED = "published"


class Event(BaseModel):
    id: str = Field(default_factory=new_id)
    request_id: str
    type: EventType
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utcnow)
