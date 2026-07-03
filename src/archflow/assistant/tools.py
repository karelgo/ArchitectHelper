"""Tool definitions and executor for the ArchiMate copilot.

Each tool mutates (or inspects) the working :class:`ViewProject`'s ArchiMate
model. Validation failures are returned as error strings — the model sees
them as tool results and self-corrects — never raised.
"""

from __future__ import annotations

import json
from typing import Any

from archflow.archimate.model import ELEMENT_TYPES, RELATIONSHIP_TYPES, ViewConnection, ViewNode
from archflow.drawio import layer_of, layered_layout
from archflow.studio.models import ViewProject

#: Anthropic tool schemas for the copilot (strict JSON schemas).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_model",
        "description": (
            "Read the current state of the working ArchiMate model: all elements "
            "(id, type, name, layer), relationships and views. Call this before "
            "modifying a model you have not inspected in this conversation."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_elements",
        "description": (
            "Add ArchiMate elements to the model. Use exact ArchiMate 3.x type "
            "names (e.g. BusinessActor, BusinessProcess, ApplicationComponent, "
            "ApplicationService, Node, Stakeholder, Driver, Goal, Capability)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "name": {"type": "string"},
                            "documentation": {"type": "string"},
                        },
                        "required": ["type", "name"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_relationships",
        "description": (
            "Add ArchiMate relationships between existing elements. Reference "
            "elements by id or exact name. Types: Composition, Aggregation, "
            "Assignment, Realization, Serving, Access, Influence, Triggering, "
            "Flow, Specialization, Association. Mind the semantics: e.g. an "
            "ApplicationService *serves* a BusinessProcess; an "
            "ApplicationComponent *realizes* an ApplicationService."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "source": {"type": "string", "description": "Element id or exact name"},
                            "target": {"type": "string", "description": "Element id or exact name"},
                            "name": {"type": "string"},
                        },
                        "required": ["type", "source", "target"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["relationships"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_elements",
        "description": (
            "Remove elements (by id or exact name) together with their "
            "relationships and view placements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "elements": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_view",
        "description": (
            "(Re)build the diagram view: places every element in layered bands "
            "(motivation → strategy → business → application → technology) and "
            "draws every relationship. Call this after the model content is in "
            "place — the user sees the result rendered in draw.io."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "View title"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "rename_model",
        "description": "Set the model's name and/or documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "documentation": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lint_model",
        "description": (
            "Check the model for ArchiMate mistakes: illegal relationship "
            "endpoints, unrealizable targets, cross-layer structure, dangling "
            "references, duplicates and orphans. Call this after building or "
            "changing the model, and fix every error it reports."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolExecutor:
    """Executes copilot tool calls against a view project's model."""

    def __init__(self, project: ViewProject) -> None:
        self.project = project
        self.actions: list[str] = []  # human-readable log for the UI
        self.changed = False

    # -- dispatch ---------------------------------------------------------------

    def execute(self, name: str, tool_input: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"Error: unknown tool {name!r}"
        return handler(tool_input)

    # -- helpers ----------------------------------------------------------------

    def _resolve(self, ref: str) -> str | None:
        """Resolve an element reference (id or exact name) to an element id."""
        model = self.project.model
        ids = {element.id for element in model.elements}
        if ref in ids:
            return ref
        matches = [e for e in model.elements if e.name == ref]
        if len(matches) == 1:
            return matches[0].id
        casefold_matches = [e for e in model.elements if e.name.casefold() == ref.casefold()]
        if len(casefold_matches) == 1:
            return casefold_matches[0].id
        return None

    def _mark_changed(self, action: str) -> None:
        self.changed = True
        self.actions.append(action)
        self.project.bump_model()

    # -- tools -------------------------------------------------------------------

    def _tool_get_model(self, _: dict[str, Any]) -> str:
        model = self.project.model
        return json.dumps(
            {
                "name": model.name,
                "elements": [
                    {"id": e.id, "type": e.type, "name": e.name, "layer": layer_of(e.type)}
                    for e in model.elements
                ],
                "relationships": [
                    {"id": r.id, "type": r.type, "source": r.source, "target": r.target}
                    for r in model.relationships
                ],
                "views": [
                    {"id": v.id, "name": v.name, "nodes": len(v.nodes)} for v in model.views
                ],
            }
        )

    def _tool_add_elements(self, tool_input: dict[str, Any]) -> str:
        model = self.project.model
        created: list[dict[str, str]] = []
        errors: list[str] = []
        for spec in tool_input.get("elements", []):
            type_ = spec.get("type", "")
            if type_ not in ELEMENT_TYPES:
                errors.append(
                    f"Unknown element type {type_!r} for {spec.get('name', '?')!r}. "
                    f"Valid types include: {', '.join(sorted(ELEMENT_TYPES))}"
                )
                continue
            element = model.add_element(
                type_, spec.get("name", ""), documentation=spec.get("documentation", "")
            )
            created.append({"id": element.id, "type": element.type, "name": element.name})
        if created:
            self._mark_changed(f"+{len(created)} element{'s' if len(created) != 1 else ''}")
        payload: dict[str, Any] = {"created": created}
        if errors:
            payload["errors"] = errors
        return json.dumps(payload)

    def _tool_add_relationships(self, tool_input: dict[str, Any]) -> str:
        model = self.project.model
        created = 0
        errors: list[str] = []
        for spec in tool_input.get("relationships", []):
            type_ = spec.get("type", "")
            if type_ not in RELATIONSHIP_TYPES:
                errors.append(
                    f"Unknown relationship type {type_!r}. Valid: "
                    f"{', '.join(sorted(RELATIONSHIP_TYPES))}"
                )
                continue
            source = self._resolve(spec.get("source", ""))
            target = self._resolve(spec.get("target", ""))
            if source is None or target is None:
                missing = spec.get("source") if source is None else spec.get("target")
                errors.append(
                    f"Element {missing!r} not found (use get_model to see ids/names; "
                    "names must match exactly and be unambiguous)"
                )
                continue
            model.add_relationship(type_, source, target, name=spec.get("name", ""))
            created += 1
        if created:
            self._mark_changed(f"+{created} relationship{'s' if created != 1 else ''}")
        payload: dict[str, Any] = {"created": created}
        if errors:
            payload["errors"] = errors
        return json.dumps(payload)

    def _tool_remove_elements(self, tool_input: dict[str, Any]) -> str:
        model = self.project.model
        removed: list[str] = []
        errors: list[str] = []
        for ref in tool_input.get("elements", []):
            element_id = self._resolve(ref)
            if element_id is None:
                errors.append(f"Element {ref!r} not found")
                continue
            model.elements = [e for e in model.elements if e.id != element_id]
            model.relationships = [
                r for r in model.relationships if element_id not in (r.source, r.target)
            ]
            remaining_rel_ids = {r.id for r in model.relationships}
            for view in model.views:
                view.nodes = [n for n in view.nodes if n.element_ref != element_id]
                node_ids = {n.id for n in view.nodes}
                view.connections = [
                    c
                    for c in view.connections
                    if c.relationship_ref in remaining_rel_ids
                    and c.source_node in node_ids
                    and c.target_node in node_ids
                ]
            removed.append(ref)
        if removed:
            # The private element-id index must follow direct list mutation.
            model.model_post_init(None)
            self._mark_changed(f"-{len(removed)} element{'s' if len(removed) != 1 else ''}")
        payload: dict[str, Any] = {"removed": removed}
        if errors:
            payload["errors"] = errors
        return json.dumps(payload)

    def _tool_create_view(self, tool_input: dict[str, Any]) -> str:
        model = self.project.model
        if not model.elements:
            return "Error: the model has no elements yet — add elements first."
        name = tool_input.get("name") or model.name or "View"
        model.views = []
        view = model.add_view(name)
        placements = layered_layout(model)
        nodes_by_element: dict[str, str] = {}
        for element in model.elements:
            x, y, w, h = placements[element.id]
            node = ViewNode(element_ref=element.id, x=x, y=y, w=w, h=h)
            view.nodes.append(node)
            nodes_by_element[element.id] = node.id
        for rel in model.relationships:
            source_node = nodes_by_element.get(rel.source)
            target_node = nodes_by_element.get(rel.target)
            if source_node and target_node:
                view.connections.append(
                    ViewConnection(
                        relationship_ref=rel.id,
                        source_node=source_node,
                        target_node=target_node,
                    )
                )
        self._mark_changed(f"view '{name}' rebuilt")
        return json.dumps(
            {"view": name, "nodes": len(view.nodes), "connections": len(view.connections)}
        )

    def _tool_lint_model(self, _: dict[str, Any]) -> str:
        from archflow.archimate.lint import lint_model, summarize

        findings = lint_model(self.project.model)
        if not findings:
            return json.dumps({"status": "clean", "findings": []})
        return json.dumps(
            {
                "status": summarize(findings),
                "findings": [
                    {"rule": f.rule, "severity": f.severity.value, "message": f.message}
                    for f in findings
                ],
            }
        )

    def _tool_rename_model(self, tool_input: dict[str, Any]) -> str:
        model = self.project.model
        if name := tool_input.get("name"):
            model.name = name
            self.project.name = name
        if (documentation := tool_input.get("documentation")) is not None:
            model.documentation = documentation
        self._mark_changed("model renamed")
        return json.dumps({"name": model.name})
