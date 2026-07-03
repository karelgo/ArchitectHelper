"""Stage assistants for the governance process.

Three drafts, all human-applied — the assistant never records verdicts or
mutates a request itself:

- **Stakeholder analysis**: proposes stakeholders/drivers/goals from the
  request description (applied via ``WorkflowEngine.apply_analysis``).
- **PSA**: drafts the Project Start Architecture prose.
- **Pre-review**: a critical read of the PSA + stakeholder map that a human
  reviewer uses to record their own verdict.

Each draft is a single forced-tool call (no loop), so the output is
structured and cheap to validate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from archflow.assistant.copilot import (
    CopilotUnavailable,
    MessagesClient,
    _default_messages_client,
)
from archflow.config import Settings
from archflow.domain.models import (
    ArchitectureRequest,
    Artifact,
    ArtifactKind,
    Assessment,
    Attitude,
    Driver,
    Goal,
    InfluenceLevel,
    ReviewVerdict,
    Stakeholder,
)

if TYPE_CHECKING:
    from archflow.workflow.engine import WorkflowEngine


def request_brief(request: ArchitectureRequest) -> str:
    """The request serialized for a prompt (also used by the Studio copilot)."""
    lines = [
        f"Title: {request.title}",
        f"Description: {request.description or '-'}",
        f"Business goal: {request.business_goal or '-'}",
        f"Requester: {request.requester or '-'}",
        f"Impacted domains: {', '.join(request.impacted_domains) or '-'}",
        f"Stage: {request.stage.value}"
        + (f" ({request.classification.value})" if request.classification else ""),
    ]
    if request.stakeholders:
        lines.append("Stakeholders:")
        lines += [
            f"  - {s.name} ({s.role or 'role unknown'}) — concerns: "
            f"{', '.join(s.concerns) or '-'} [influence {s.influence.value}, "
            f"interest {s.interest.value}, attitude {s.attitude.value}]"
            for s in request.stakeholders
        ]
    if request.drivers:
        lines.append("Drivers: " + "; ".join(d.name for d in request.drivers))
    if request.goals:
        lines.append("Goals: " + "; ".join(g.name for g in request.goals))
    if request.decisions:
        lines.append("Decisions:")
        lines += [f"  - [{d.status.value}] {d.title}: {d.rationale or '-'}" for d in request.decisions]
    return "\n".join(lines)


# --- structured outputs -------------------------------------------------------------


class ProposedStakeholder(BaseModel):
    name: str
    role: str = ""
    concerns: list[str] = Field(default_factory=list)
    influence: InfluenceLevel = InfluenceLevel.MEDIUM
    interest: InfluenceLevel = InfluenceLevel.MEDIUM
    attitude: Attitude = Attitude.NEUTRAL
    rationale: str = ""


class ProposedNamed(BaseModel):
    name: str
    description: str = ""


class StakeholderProposal(BaseModel):
    stakeholders: list[ProposedStakeholder] = Field(default_factory=list)
    drivers: list[ProposedNamed] = Field(default_factory=list)
    goals: list[ProposedNamed] = Field(default_factory=list)
    assessments: list[ProposedNamed] = Field(default_factory=list)
    notes: str = ""


class ReviewFinding(BaseModel):
    severity: str = "minor"  # blocking | major | minor
    area: str = ""  # completeness | consistency | feasibility | clarity | model
    message: str


class ReviewDraft(BaseModel):
    suggested_verdict: ReviewVerdict
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class PsaDraft(BaseModel):
    markdown: str
    saved_path: str | None = None


# --- tool schemas ---------------------------------------------------------------------

_LEVELS = {"type": "string", "enum": ["low", "medium", "high"]}

_STAKEHOLDER_TOOL: dict[str, Any] = {
    "name": "submit_stakeholder_analysis",
    "description": "Submit the proposed stakeholder analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stakeholders": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                        "concerns": {"type": "array", "items": {"type": "string"}},
                        "influence": _LEVELS,
                        "interest": _LEVELS,
                        "attitude": {
                            "type": "string",
                            "enum": ["champion", "supportive", "neutral", "critical", "blocker"],
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            "drivers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "goals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["stakeholders"],
    },
}

_REVIEW_TOOL: dict[str, Any] = {
    "name": "submit_review",
    "description": "Submit the peer-review draft.",
    "input_schema": {
        "type": "object",
        "properties": {
            "suggested_verdict": {"type": "string", "enum": ["approve", "request_changes"]},
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
                        "area": {
                            "type": "string",
                            "enum": ["completeness", "consistency", "feasibility", "clarity", "model"],
                        },
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
            },
        },
        "required": ["suggested_verdict", "summary"],
    },
}

_ANALYST_SYSTEM = """\
You are an enterprise-architecture stakeholder analyst. From the request
material, propose a stakeholder analysis for an architecture governance
process. Include silent-but-affected parties (operations, security,
compliance, works council) as ROLES — never invent named individuals; only
use personal names that appear in the material. Concerns must be concrete
(what this change could cost or gain them). Also propose drivers (forces
behind the request), goals (measurable end states) and assessments (observed
facts), each traceable to the material. Put any assumptions in `notes`.
Submit via the tool."""

_PSA_SYSTEM = """\
You are a senior enterprise architect writing a Project Start Architecture
(PSA). Write crisp, decision-oriented markdown for the sections: Context,
Scope (in/out), Architecture considerations per impacted domain, Key
decisions with consequences, Risks & mitigations, and Open questions. Ground
every statement in the request material; where information is missing, add
an explicit open question instead of inventing facts. Do not include a
document title header (it is added by the tooling)."""

_REVIEWER_SYSTEM = """\
You are a critical peer reviewer of architecture deliverables — a friendly
nitpicker, not a rubber stamp. Review the PSA and stakeholder analysis on:
completeness (missing domains/stakeholders?), consistency (do decisions serve
the goals and address concerns?), feasibility (dependencies, sequencing),
clarity (can a team start from this?), and model quality. Report every issue
you find, including ones you are uncertain about — include severity so a
human can filter. Suggest request_changes when any blocking finding exists.
Your draft informs a human reviewer; you are not the one deciding. Submit
via the tool."""


class GovernanceAssistant:
    """One-shot drafting assistants for governance stages."""

    def __init__(
        self,
        settings: Settings,
        messages_client: MessagesClient | None = None,
    ) -> None:
        if messages_client is None and not settings.assistant_configured:
            raise CopilotUnavailable(
                "No Anthropic API key configured. Set ARCHFLOW_ANTHROPIC_API_KEY "
                "to enable AI drafts."
            )
        self._settings = settings
        self._messages = messages_client or _default_messages_client(settings)

    def _forced_tool_call(
        self, system: str, user_text: str, tool: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._messages.create(
            model=self._settings.assistant_model,
            max_tokens=8192,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user_text}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input or {})
        raise ValueError("Assistant did not produce the expected structured output")

    def draft_stakeholder_analysis(self, request: ArchitectureRequest) -> StakeholderProposal:
        """Propose stakeholders/drivers/goals/assessments for the request."""
        payload = self._forced_tool_call(
            _ANALYST_SYSTEM,
            f"Architecture request:\n\n{request_brief(request)}",
            _STAKEHOLDER_TOOL,
        )
        try:
            return StakeholderProposal.model_validate(payload)
        except ValidationError as err:
            raise ValueError(f"Assistant returned an invalid proposal: {err}") from err

    def draft_psa(self, request: ArchitectureRequest, current_psa: str = "") -> str:
        """Draft the PSA prose as markdown."""
        prompt = f"Architecture request:\n\n{request_brief(request)}"
        if current_psa:
            prompt += (
                "\n\nThe current (template-generated) PSA below contains the factual "
                "tables — improve and extend it with real architectural prose, keeping "
                "the facts:\n\n" + current_psa
            )
        response = self._messages.create(
            model=self._settings.assistant_model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {"type": "text", "text": _PSA_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ValueError("Assistant returned an empty PSA draft")
        return text

    def pre_review(self, request: ArchitectureRequest, psa_text: str = "") -> ReviewDraft:
        """Draft a peer review of the request's deliverables."""
        prompt = f"Architecture request:\n\n{request_brief(request)}"
        if psa_text:
            prompt += f"\n\nProject Start Architecture under review:\n\n{psa_text}"
        else:
            prompt += "\n\nNote: no PSA document was found — treat that as a finding."
        payload = self._forced_tool_call(_REVIEWER_SYSTEM, prompt, _REVIEW_TOOL)
        try:
            return ReviewDraft.model_validate(payload)
        except ValidationError as err:
            raise ValueError(f"Assistant returned an invalid review: {err}") from err


def existing_psa_text(request: ArchitectureRequest) -> str:
    """The request's current PSA document, or empty when there is none."""
    artifact = request.artifact_of_kind(ArtifactKind.PSA_DOCUMENT)
    if artifact and Path(artifact.path).exists():
        return Path(artifact.path).read_text(encoding="utf-8")
    return ""


def save_psa_draft(
    engine: WorkflowEngine,
    settings: Settings,
    request: ArchitectureRequest,
    markdown: str,
    actor: str = "assistant",
) -> str:
    """Persist an approved PSA draft as the request's PSA artifact."""
    directory = settings.artifacts_dir / request.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "psa.md"
    path.write_text(markdown, encoding="utf-8")
    engine.record_artifact(
        request.id,
        Artifact(
            kind=ArtifactKind.PSA_DOCUMENT,
            name=f"Project Start Architecture — {request.title} (AI draft)",
            path=str(path),
        ),
        actor=actor,
    )
    return str(path)


def proposal_to_domain(
    proposal: StakeholderProposal,
) -> tuple[list[Stakeholder], list[Driver], list[Goal], list[Assessment]]:
    """Convert a proposal into domain objects ready for ``apply_analysis``."""
    stakeholders = [
        Stakeholder(
            name=p.name,
            role=p.role,
            concerns=p.concerns,
            influence=p.influence,
            interest=p.interest,
            attitude=p.attitude,
        )
        for p in proposal.stakeholders
    ]
    drivers = [Driver(name=d.name, description=d.description) for d in proposal.drivers]
    goals = [Goal(name=g.name, description=g.description) for g in proposal.goals]
    assessments = [
        Assessment(name=a.name, description=a.description) for a in proposal.assessments
    ]
    return stakeholders, drivers, goals, assessments
