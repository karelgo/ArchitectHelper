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
    """Maps pipeline stages to automation actions.

    *On-enter* actions produce a first version of a stage's artifacts the
    moment the stage starts. *On-exit* actions run after the stage's gate has
    passed, just before the transition — they regenerate artifacts so the
    recorded deliverable reflects everything added *during* the stage
    (stakeholders, decisions) instead of a stale entry-time snapshot.
    """

    def __init__(self) -> None:
        self._on_enter: dict[Stage, list[Action]] = {}
        self._on_exit: dict[Stage, list[Action]] = {}

    def register(self, stage: Stage, fn: Action) -> None:
        """Register an action to run when a request enters ``stage``."""
        self._on_enter.setdefault(stage, []).append(fn)

    def register_exit(self, stage: Stage, fn: Action) -> None:
        """Register an action to run when a request leaves ``stage``."""
        self._on_exit.setdefault(stage, []).append(fn)

    def on_enter(self, stage: Stage, ctx: AutomationContext) -> list[Artifact]:
        """Run all on-enter actions for ``stage``, collecting artifacts.

        Exceptions raised by actions propagate to the caller.
        """
        return self._run(self._on_enter.get(stage, []), ctx)

    def on_exit(self, stage: Stage, ctx: AutomationContext) -> list[Artifact]:
        """Run all on-exit actions for ``stage``, collecting artifacts."""
        return self._run(self._on_exit.get(stage, []), ctx)

    @staticmethod
    def _run(actions: list[Action], ctx: AutomationContext) -> list[Artifact]:
        artifacts: list[Artifact] = []
        for fn in actions:
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
    """The standard wiring of automation actions to pipeline stages.

    Map and PSA are generated on entry (an immediate draft to work with) and
    regenerated on exit, so the artifact that travels to the next stage
    reflects the stakeholders/decisions recorded during the stage.
    """
    registry = ActionRegistry()
    registry.register(Stage.STAKEHOLDER_ANALYSIS, generate_stakeholder_map_action)
    registry.register_exit(Stage.STAKEHOLDER_ANALYSIS, generate_stakeholder_map_action)
    registry.register(Stage.DRAFTING, generate_psa_action)
    registry.register_exit(Stage.DRAFTING, generate_psa_action)
    registry.register(Stage.PUBLICATION, publish_action)
    return registry
