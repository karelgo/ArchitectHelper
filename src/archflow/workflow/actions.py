"""Stage-entry automation actions that generate artifacts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archflow.config import Settings
from archflow.domain.models import ArchitectureRequest, Artifact, ArtifactKind, Stage


class AutomationContext(BaseModel):
    """Everything an automation action needs to do its work."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request: ArchitectureRequest
    settings: Settings
    artifacts_dir: Path


Action = Callable[[AutomationContext], list[Artifact]]


class ActionRegistry:
    """Maps pipeline stages to automation actions run on stage entry."""

    def __init__(self) -> None:
        self._actions: dict[Stage, list[Action]] = {}

    def register(self, stage: Stage, fn: Action) -> None:
        """Register an action to run when a request enters ``stage``."""
        self._actions.setdefault(stage, []).append(fn)

    def on_enter(self, stage: Stage, ctx: AutomationContext) -> list[Artifact]:
        """Run all actions for ``stage``, collecting the generated artifacts.

        Exceptions raised by actions propagate to the caller.
        """
        artifacts: list[Artifact] = []
        for fn in self._actions.get(stage, []):
            artifacts.extend(fn(ctx))
        return artifacts


def _request_dir(ctx: AutomationContext) -> Path:
    """Per-request artifacts subdirectory, created on demand."""
    directory = ctx.artifacts_dir / ctx.request.id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def generate_stakeholder_map_action(ctx: AutomationContext) -> list[Artifact]:
    """Generate the ArchiMate stakeholder map for the request."""
    from archflow.archimate.stakeholder_map import build_stakeholder_map

    model = build_stakeholder_map(ctx.request)
    xml = model.to_open_exchange_xml()
    path = _request_dir(ctx) / "stakeholder_map.archimate.xml"
    path.write_text(xml, encoding="utf-8")
    return [
        Artifact(
            kind=ArtifactKind.STAKEHOLDER_MAP,
            name=f"Stakeholder map — {ctx.request.title}",
            path=str(path),
        )
    ]


def generate_psa_action(ctx: AutomationContext) -> list[Artifact]:
    """Render the Project Start Architecture document."""
    from archflow.workflow.documents import render_psa

    content = render_psa(ctx.request)
    path = _request_dir(ctx) / "psa.md"
    path.write_text(content, encoding="utf-8")
    return [
        Artifact(
            kind=ArtifactKind.PSA_DOCUMENT,
            name=f"Project Start Architecture — {ctx.request.title}",
            path=str(path),
        )
    ]


def publish_action(ctx: AutomationContext) -> list[Artifact]:
    """Publish the request via BiZZdesign Horizzon (or file export fallback)."""
    from archflow.horizzon.publisher import HorizzonPublisher

    result = HorizzonPublisher(ctx.settings).publish(ctx.request)
    if result.artifact_path:
        return [
            Artifact(
                kind=ArtifactKind.ARCHIMATE_EXPORT,
                name=f"ArchiMate export ({result.mode})",
                path=str(result.artifact_path),
            )
        ]
    return []


def default_registry() -> ActionRegistry:
    """The standard wiring of automation actions to pipeline stages."""
    registry = ActionRegistry()
    registry.register(Stage.STAKEHOLDER_ANALYSIS, generate_stakeholder_map_action)
    registry.register(Stage.DRAFTING, generate_psa_action)
    registry.register(Stage.PUBLICATION, publish_action)
    return registry
