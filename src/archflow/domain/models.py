"""Core domain models for the architecture governance process.

An :class:`ArchitectureRequest` is the aggregate root: one request moves
through the :class:`Stage` pipeline from intake to publication, collecting
stakeholders, assessments, artifacts, reviews and decisions along the way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def new_id() -> str:
    """Generate a unique identifier for domain objects."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Timezone-aware current time; the only clock the domain uses."""
    return datetime.now(UTC)


class Stage(StrEnum):
    """Stages of the architecture governance process, in pipeline order.

    ``REJECTED`` is a terminal side-exit reachable from any active stage.
    """

    INTAKE = "intake"
    TRIAGE = "triage"
    STAKEHOLDER_ANALYSIS = "stakeholder_analysis"
    DRAFTING = "drafting"
    PEER_REVIEW = "peer_review"
    BOARD_APPROVAL = "board_approval"
    PUBLICATION = "publication"
    DONE = "done"
    REJECTED = "rejected"


#: Active pipeline order (terminal stages excluded). Fast-track requests skip
#: BOARD_APPROVAL — see :func:`next_stage`.
PIPELINE: tuple[Stage, ...] = (
    Stage.INTAKE,
    Stage.TRIAGE,
    Stage.STAKEHOLDER_ANALYSIS,
    Stage.DRAFTING,
    Stage.PEER_REVIEW,
    Stage.BOARD_APPROVAL,
    Stage.PUBLICATION,
    Stage.DONE,
)


class Classification(StrEnum):
    """Triage outcome deciding how heavy the governance track is."""

    SMALL = "small"  # fast-track: peer review only, no board approval
    MEDIUM = "medium"
    LARGE = "large"


class InfluenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Attitude(StrEnum):
    """Stakeholder disposition towards the change."""

    CHAMPION = "champion"
    SUPPORTIVE = "supportive"
    NEUTRAL = "neutral"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class Stakeholder(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    role: str = ""
    concerns: list[str] = Field(default_factory=list)
    influence: InfluenceLevel = InfluenceLevel.MEDIUM
    interest: InfluenceLevel = InfluenceLevel.MEDIUM
    attitude: Attitude = Attitude.NEUTRAL


class Driver(BaseModel):
    """Motivation-layer driver: an internal or external force behind the request."""

    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    stakeholder_names: list[str] = Field(default_factory=list)


class Goal(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    stakeholder_names: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    """Motivation-layer assessment: an observed fact about a driver."""

    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    driver_name: str = ""


class ArtifactKind(StrEnum):
    PSA_DOCUMENT = "psa_document"  # project start architecture
    STAKEHOLDER_MAP = "stakeholder_map"  # ArchiMate motivation view
    ARCHIMATE_EXPORT = "archimate_export"  # Open Exchange XML file
    DECISION_LOG = "decision_log"
    REVIEW_REPORT = "review_report"


class Artifact(BaseModel):
    """A generated deliverable, stored on disk under the artifacts directory."""

    id: str = Field(default_factory=new_id)
    kind: ArtifactKind
    name: str
    path: str
    created_at: datetime = Field(default_factory=utcnow)


class ChecklistItem(BaseModel):
    """A gate condition that must be satisfied before leaving a stage."""

    key: str
    description: str
    stage: Stage
    done: bool = False
    completed_by: str = ""
    completed_at: datetime | None = None


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class Review(BaseModel):
    id: str = Field(default_factory=new_id)
    reviewer: str
    verdict: ReviewVerdict
    comments: str = ""
    stage: Stage = Stage.PEER_REVIEW
    created_at: datetime = Field(default_factory=utcnow)


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class Decision(BaseModel):
    """An architecture decision recorded against the request (ADR-style).

    ``stage`` records where in the pipeline the decision was made — the
    board-approval gate only counts approvals recorded *at* board approval,
    so an early "approved" decision cannot pre-satisfy the gate.
    """

    id: str = Field(default_factory=new_id)
    title: str
    rationale: str = ""
    status: DecisionStatus = DecisionStatus.PROPOSED
    decided_by: str = ""
    decided_at: datetime | None = None
    stage: Stage | None = None


class ArchitectureRequest(BaseModel):
    """Aggregate root: one architecture change moving through the process."""

    id: str = Field(default_factory=new_id)
    title: str
    description: str = ""
    requester: str = ""
    business_goal: str = ""
    impacted_domains: list[str] = Field(default_factory=list)
    classification: Classification | None = None
    stage: Stage = Stage.INTAKE

    stakeholders: list[Stakeholder] = Field(default_factory=list)
    drivers: list[Driver] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)

    artifacts: list[Artifact] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()

    @property
    def is_fast_track(self) -> bool:
        return self.classification == Classification.SMALL

    def open_checklist(self, stage: Stage | None = None) -> list[ChecklistItem]:
        """Unfinished checklist items, optionally limited to one stage."""
        items = [i for i in self.checklist if not i.done]
        if stage is not None:
            items = [i for i in items if i.stage == stage]
        return items

    def artifact_of_kind(self, kind: ArtifactKind) -> Artifact | None:
        """Most recent artifact of the given kind, if any.

        Ties on ``created_at`` (clock resolution) resolve to the one added
        last, since artifacts are appended in generation order.
        """
        matches = [(i, a) for i, a in enumerate(self.artifacts) if a.kind == kind]
        if not matches:
            return None
        return max(matches, key=lambda pair: (pair[1].created_at, pair[0]))[1]


def next_stage(request: ArchitectureRequest) -> Stage | None:
    """The stage that follows the request's current stage in the pipeline.

    Fast-track (small) requests skip board approval. Returns ``None`` when
    the request is already in a terminal stage.
    """
    current = request.stage
    if current in (Stage.DONE, Stage.REJECTED):
        return None
    idx = PIPELINE.index(current)
    upcoming = PIPELINE[idx + 1]
    if upcoming == Stage.BOARD_APPROVAL and request.is_fast_track:
        return Stage.PUBLICATION
    return upcoming
