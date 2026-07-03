"""ArchiMate model linter: semantic and structural checks.

Rules are deliberately conservative — they flag only combinations that are
clearly wrong under the ArchiMate 3.x specification's element categories
(active structure / behavior / passive structure / motivation), so a clean
model stays clean and a red finding is worth trusting. Severities:

- ``error``   — definitely invalid (illegal endpoints, dangling references)
- ``warning`` — almost always a mistake (cross-layer composition, orphans)
- ``info``    — worth a look (element not placed on any view)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum

from pydantic import BaseModel, Field

from archflow.archimate.model import ArchimateModel, Relationship

# --- element categories (ArchiMate 3.x, restricted to our type catalogue) -----

ACTIVE_STRUCTURE: frozenset[str] = frozenset(
    {
        "BusinessActor",
        "BusinessRole",
        "BusinessCollaboration",
        "BusinessInterface",
        "ApplicationComponent",
        "ApplicationCollaboration",
        "ApplicationInterface",
        "Node",
        "Device",
        "SystemSoftware",
    }
)

BEHAVIOR: frozenset[str] = frozenset(
    {
        "BusinessProcess",
        "BusinessFunction",
        "BusinessInteraction",
        "BusinessEvent",
        "BusinessService",
        "ApplicationProcess",
        "ApplicationFunction",
        "ApplicationInteraction",
        "ApplicationEvent",
        "ApplicationService",
        "TechnologyService",
        "Capability",
        "ValueStream",
        "CourseOfAction",
    }
)

PASSIVE_STRUCTURE: frozenset[str] = frozenset(
    {"BusinessObject", "Contract", "Representation", "DataObject", "Artifact"}
)

MOTIVATION: frozenset[str] = frozenset(
    {
        "Stakeholder",
        "Driver",
        "Assessment",
        "Goal",
        "Outcome",
        "Principle",
        "Requirement",
        "Constraint",
        "Meaning",
        "Value",
    }
)

#: Motivation elements that describe the world rather than an intention —
#: nothing can "realize" a stakeholder or an observed fact.
_UNREALIZABLE: frozenset[str] = frozenset({"Stakeholder", "Driver", "Assessment"})

#: Grouping legitimately crosses every boundary; skip cross-layer checks on it.
_BOUNDARY_EXEMPT: frozenset[str] = frozenset({"Grouping", "Location"})


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class LintFinding(BaseModel):
    rule: str
    severity: Severity
    message: str
    element_ids: list[str] = Field(default_factory=list)
    relationship_id: str | None = None


def lint_model(model: ArchimateModel) -> list[LintFinding]:
    """Run all lint rules; findings come back errors first, stable order."""
    findings: list[LintFinding] = []
    types = {element.id: element.type for element in model.elements}
    names = {element.id: element.name for element in model.elements}

    def describe(rel: Relationship) -> str:
        return (
            f"{types.get(rel.source, '?')} '{names.get(rel.source, rel.source)}' "
            f"-{rel.type}-> {types.get(rel.target, '?')} '{names.get(rel.target, rel.target)}'"
        )

    findings += _lint_relationship_semantics(model, types, describe)
    findings += _lint_structure(model, types)
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: order[f.severity])
    return findings


# --- semantic rules ---------------------------------------------------------------


def _lint_relationship_semantics(
    model: ArchimateModel,
    types: dict[str, str],
    describe: Callable[[Relationship], str],
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for rel in model.relationships:
        source_type = types.get(rel.source)
        target_type = types.get(rel.target)
        if source_type is None or target_type is None:
            continue  # dangling endpoints are reported by the structural pass

        def flag(rule: str, severity: Severity, message: str, rel: Relationship = rel) -> None:
            findings.append(
                LintFinding(
                    rule=rule,
                    severity=severity,
                    message=f"{message}: {describe(rel)}",
                    element_ids=[rel.source, rel.target],
                    relationship_id=rel.id,
                )
            )

        if rel.type == "Influence" and target_type not in MOTIVATION:
            flag(
                "influence-target",
                Severity.ERROR,
                "Influence must point at a motivation element",
            )

        if rel.type == "Access":
            if target_type not in PASSIVE_STRUCTURE:
                flag(
                    "access-target",
                    Severity.ERROR,
                    "Access must point at a passive structure element (object/data/artifact)",
                )
            elif source_type not in BEHAVIOR and source_type not in ACTIVE_STRUCTURE:
                flag(
                    "access-source",
                    Severity.WARNING,
                    "Access is normally performed by behavior (or derived via active structure)",
                )

        if rel.type == "Realization" and target_type in _UNREALIZABLE:
            flag(
                "unrealizable-target",
                Severity.ERROR,
                "Stakeholders, drivers and assessments cannot be realized",
            )

        if rel.type == "Assignment" and source_type not in ACTIVE_STRUCTURE:
            flag(
                "assignment-source",
                Severity.WARNING,
                "Assignment is normally from an active structure element (actor/role/component/node)",
            )

        if rel.type in ("Triggering", "Flow") and (
            target_type in PASSIVE_STRUCTURE
            or source_type in PASSIVE_STRUCTURE
            or target_type in MOTIVATION
            or source_type in MOTIVATION
        ):
            flag(
                "flow-endpoints",
                Severity.WARNING,
                f"{rel.type} connects behavior (or active structure), not passive/motivation elements",
            )

        if rel.type == "Specialization" and source_type != target_type:
            flag(
                "specialization-type",
                Severity.WARNING,
                "Specialization normally relates two elements of the same type",
            )

        if (
            rel.type in ("Composition", "Aggregation")
            and source_type not in _BOUNDARY_EXEMPT
            and target_type not in _BOUNDARY_EXEMPT
        ):
            from archflow.drawio import layer_of

            if layer_of(source_type) != layer_of(target_type):
                flag(
                    "cross-layer-structure",
                    Severity.WARNING,
                    "Composition/aggregation across layers is usually a modeling mistake",
                )
    return findings


# --- structural rules ----------------------------------------------------------------


def _lint_structure(model: ArchimateModel, types: dict[str, str]) -> list[LintFinding]:
    findings: list[LintFinding] = []

    seen: dict[tuple[str, str], str] = {}
    for element in model.elements:
        if not element.name.strip():
            findings.append(
                LintFinding(
                    rule="empty-name",
                    severity=Severity.WARNING,
                    message=f"{element.type} element has no name",
                    element_ids=[element.id],
                )
            )
            continue
        key = (element.type, element.name.strip().casefold())
        if key in seen:
            findings.append(
                LintFinding(
                    rule="duplicate-name",
                    severity=Severity.WARNING,
                    message=f"Duplicate {element.type} named '{element.name}'",
                    element_ids=[seen[key], element.id],
                )
            )
        else:
            seen[key] = element.id

    connected: set[str] = set()
    for rel in model.relationships:
        connected.add(rel.source)
        connected.add(rel.target)
        missing = [ref for ref in (rel.source, rel.target) if ref not in types]
        if missing:
            findings.append(
                LintFinding(
                    rule="dangling-relationship",
                    severity=Severity.ERROR,
                    message=f"{rel.type} relationship references missing element(s)",
                    element_ids=missing,
                    relationship_id=rel.id,
                )
            )

    if len(model.elements) > 1:
        for element in model.elements:
            if element.id not in connected:
                findings.append(
                    LintFinding(
                        rule="orphan-element",
                        severity=Severity.WARNING,
                        message=f"{element.type} '{element.name}' has no relationships",
                        element_ids=[element.id],
                    )
                )

    relationship_ids = {rel.id for rel in model.relationships}
    placed: set[str] = set()
    for view in model.views:
        node_ids = {node.id for node in view.nodes}
        for node in view.nodes:
            placed.add(node.element_ref)
            if node.element_ref and node.element_ref not in types:
                findings.append(
                    LintFinding(
                        rule="dangling-view-node",
                        severity=Severity.ERROR,
                        message=f"View '{view.name}' places a missing element",
                        element_ids=[node.element_ref],
                    )
                )
        for connection in view.connections:
            if (
                connection.relationship_ref not in relationship_ids
                or connection.source_node not in node_ids
                or connection.target_node not in node_ids
            ):
                findings.append(
                    LintFinding(
                        rule="dangling-view-connection",
                        severity=Severity.ERROR,
                        message=f"View '{view.name}' has a connection with missing endpoints",
                        relationship_id=connection.relationship_ref,
                    )
                )

    if model.views:
        for element in model.elements:
            if element.id not in placed:
                findings.append(
                    LintFinding(
                        rule="not-on-view",
                        severity=Severity.INFO,
                        message=f"{element.type} '{element.name}' is not placed on any view",
                        element_ids=[element.id],
                    )
                )

    return findings


def summarize(findings: Iterable[LintFinding]) -> str:
    """One-line summary, e.g. '2 errors, 1 warning'. Empty string when clean."""
    counts: dict[Severity, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    parts = [
        f"{count} {severity.value}{'s' if count != 1 else ''}"
        for severity, count in counts.items()
    ]
    return ", ".join(parts)
