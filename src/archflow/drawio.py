"""ArchiMate model ⇄ draw.io (mxGraph) diagram conversion.

Renders an :class:`~archflow.archimate.model.ArchimateModel` as a draw.io
diagram with ArchiMate layer colours and correct relationship notations, and
syncs geometry edits made in draw.io back into the model's view. Cell ids are
stable (``el-<element id>`` / ``rel-<relationship id>``) so a diagram edited
in draw.io can always be matched back to the model.

Deliberately uses plain draw.io shapes (rounded rectangles + layer fills +
``«Type»`` stereotype labels) rather than the mxgraph ArchiMate stencils:
plain shapes render identically in every draw.io deployment, and users can
restyle freely in the editor without breaking the model mapping.
"""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET

from archflow.archimate.model import ArchimateModel, View, ViewNode

#: ArchiMate layer fill colours (per the standard's conventional palette).
_LAYER_FILLS: dict[str, str] = {
    "motivation": "#CCCCFF",
    "strategy": "#F5DEAA",
    "business": "#FFFF99",
    "application": "#99FFFF",
    "technology": "#AFFFAF",
    "implementation": "#FFE0E0",
    "other": "#F5F5F5",
}

_MOTIVATION = {
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
_STRATEGY = {"Resource", "Capability", "CourseOfAction", "ValueStream"}
_BUSINESS = {
    "BusinessActor",
    "BusinessRole",
    "BusinessCollaboration",
    "BusinessInterface",
    "BusinessProcess",
    "BusinessFunction",
    "BusinessInteraction",
    "BusinessEvent",
    "BusinessService",
    "BusinessObject",
    "Contract",
    "Representation",
    "Product",
}
_APPLICATION = {
    "ApplicationComponent",
    "ApplicationCollaboration",
    "ApplicationInterface",
    "ApplicationFunction",
    "ApplicationInteraction",
    "ApplicationProcess",
    "ApplicationEvent",
    "ApplicationService",
    "DataObject",
}
_TECHNOLOGY = {"Node", "Device", "SystemSoftware", "TechnologyService", "Artifact"}
_IMPLEMENTATION = {"WorkPackage", "Deliverable", "Plateau", "Gap"}

#: Band order used for auto-layout when a model has no view geometry.
_LAYER_ORDER = (
    "motivation",
    "strategy",
    "business",
    "application",
    "technology",
    "implementation",
    "other",
)

#: draw.io edge styles per ArchiMate relationship type (standard notation).
_EDGE_STYLES: dict[str, str] = {
    "Association": "endArrow=none;",
    "Influence": "dashed=1;endArrow=open;endFill=0;",
    "Realization": "dashed=1;endArrow=block;endFill=0;",
    "Serving": "endArrow=open;endFill=0;",
    "Assignment": "startArrow=oval;startFill=1;endArrow=block;endFill=1;",
    "Composition": "startArrow=diamondThin;startFill=1;startSize=14;endArrow=none;",
    "Aggregation": "startArrow=diamondThin;startFill=0;startSize=14;endArrow=none;",
    "Triggering": "endArrow=block;endFill=1;",
    "Flow": "dashed=1;endArrow=block;endFill=1;",
    "Access": "dashed=1;dashPattern=1 4;endArrow=open;endFill=0;",
    "Specialization": "endArrow=block;endFill=0;endSize=12;",
}

_EDGE_BASE = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;jettySize=auto;orthogonalLoop=1;"


def layer_of(element_type: str) -> str:
    """The ArchiMate layer a given element type belongs to."""
    if element_type in _MOTIVATION:
        return "motivation"
    if element_type in _STRATEGY:
        return "strategy"
    if element_type in _BUSINESS:
        return "business"
    if element_type in _APPLICATION:
        return "application"
    if element_type in _TECHNOLOGY:
        return "technology"
    if element_type in _IMPLEMENTATION:
        return "implementation"
    return "other"


def _vertex_style(element_type: str) -> str:
    fill = _LAYER_FILLS[layer_of(element_type)]
    return (
        "rounded=1;whiteSpace=wrap;html=1;arcSize=8;"
        f"fillColor={fill};strokeColor=#5B5B5B;fontColor=#1A1A1A;"
        "verticalAlign=middle;align=center;"
    )


def _vertex_label(name: str, element_type: str) -> str:
    safe_name = html.escape(name)
    safe_type = html.escape(element_type)
    return f"<b>{safe_name}</b><br/><font style=\"font-size:10px\">&#171;{safe_type}&#187;</font>"


def layered_layout(model: ArchimateModel) -> dict[str, tuple[int, int, int, int]]:
    """Grid placement per element id, banded by ArchiMate layer.

    Public so view-building code (e.g. the copilot's ``create_view`` tool)
    lays out views the same way the diagram renderer does.
    """
    return _auto_layout(model)


def _auto_layout(model: ArchimateModel) -> dict[str, tuple[int, int, int, int]]:
    """Grid placement per element id, banded by ArchiMate layer."""
    per_band: dict[str, list[str]] = {band: [] for band in _LAYER_ORDER}
    for element in model.elements:
        per_band[layer_of(element.type)].append(element.id)

    placements: dict[str, tuple[int, int, int, int]] = {}
    columns, width, height, h_gap, v_gap = 4, 200, 60, 40, 40
    y = 40
    for band in _LAYER_ORDER:
        ids = per_band[band]
        if not ids:
            continue
        for index, element_id in enumerate(ids):
            row, col = divmod(index, columns)
            placements[element_id] = (
                40 + col * (width + h_gap),
                y + row * (height + v_gap),
                width,
                height,
            )
        rows = (len(ids) + columns - 1) // columns
        y += rows * (height + v_gap) + 40
    return placements


def _resolve_geometry(
    model: ArchimateModel, view: View | None
) -> dict[str, tuple[int, int, int, int]]:
    """Geometry per element id: from the view when given, else auto-layout."""
    if view is None:
        return _auto_layout(model)
    placements = {
        node.element_ref: (node.x, node.y, node.w, node.h)
        for node in view.nodes
        if node.element_ref
    }
    # Elements missing from the view still get a slot so nothing vanishes.
    missing = [e for e in model.elements if e.id not in placements]
    if missing:
        y = max((p[1] + p[3] for p in placements.values()), default=0) + 80
        for index, element in enumerate(missing):
            row, col = divmod(index, 4)
            placements[element.id] = (40 + col * 240, y + row * 100, 200, 60)
    return placements


def to_drawio_xml(model: ArchimateModel, view: View | None = None) -> str:
    """Render a model (optionally one specific view) as draw.io mxfile XML."""
    if view is None and model.views:
        view = model.views[0]
    placements = _resolve_geometry(model, view)

    mxfile = ET.Element("mxfile", {"host": "archflow", "agent": "archflow"})
    diagram = ET.SubElement(
        mxfile, "diagram", {"id": "archflow-1", "name": view.name if view else model.name}
    )
    graph = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1000",
            "dy": "700",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": "1169",
            "pageHeight": "826",
        },
    )
    root = ET.SubElement(graph, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    for element in model.elements:
        x, y, w, h = placements[element.id]
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"el-{element.id}",
                "value": _vertex_label(element.name, element.type),
                "style": _vertex_style(element.type),
                "vertex": "1",
                "parent": "1",
            },
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry"},
        )

    element_ids = {element.id for element in model.elements}
    for relationship in model.relationships:
        if relationship.source not in element_ids or relationship.target not in element_ids:
            continue
        style = _EDGE_BASE + _EDGE_STYLES.get(relationship.type, "endArrow=open;")
        cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": f"rel-{relationship.id}",
                "value": html.escape(relationship.name),
                "style": style,
                "edge": "1",
                "parent": "1",
                "source": f"el-{relationship.source}",
                "target": f"el-{relationship.target}",
            },
        )
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})

    ET.indent(mxfile, space="  ")
    return ET.tostring(mxfile, encoding="unicode")


def apply_drawio_geometry(model: ArchimateModel, drawio_xml: str) -> int:
    """Sync node positions/sizes edited in draw.io back into the model's view.

    Matches draw.io cells to elements via the ``el-<id>`` convention; cells
    added in draw.io that don't correspond to model elements are ignored.
    Creates the view (and missing view nodes) as needed. Returns the number
    of view nodes created or updated.
    """
    root = ET.fromstring(drawio_xml)
    geometry: dict[str, tuple[int, int, int, int]] = {}
    for cell in root.iter("mxCell"):
        cell_id = cell.get("id", "")
        if not cell_id.startswith("el-") or cell.get("vertex") != "1":
            continue
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        geometry[cell_id.removeprefix("el-")] = (
            int(float(geo.get("x", "0"))),
            int(float(geo.get("y", "0"))),
            int(float(geo.get("width", "120"))),
            int(float(geo.get("height", "55"))),
        )

    if not geometry:
        return 0

    view = model.views[0] if model.views else model.add_view(model.name)

    element_ids = {element.id for element in model.elements}
    nodes_by_element = {node.element_ref: node for node in view.nodes}
    changed = 0
    for element_id, (x, y, w, h) in geometry.items():
        if element_id not in element_ids:
            continue
        node = nodes_by_element.get(element_id)
        if node is None:
            view.nodes.append(ViewNode(element_ref=element_id, x=x, y=y, w=w, h=h))
            changed += 1
        elif (node.x, node.y, node.w, node.h) != (x, y, w, h):
            node.x, node.y, node.w, node.h = x, y, w, h
            changed += 1
    return changed
