"""Server-side SVG rendering of ArchiMate views.

Small, dependency-free previews: gallery thumbnails, the stakeholder-map
preview in the request drawer, and the Studio's canvas fallback when the
draw.io embed cannot load. Fidelity is deliberately modest — boxes in
layer colours with named connections — the real notation lives in draw.io.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from archflow.archimate.model import ArchimateModel, View

_FONT = "'IBM Plex Sans', 'Segoe UI', sans-serif"
_INK = "#1e2e36"
_LINE = "#51626b"
_PAPER = "#ffffff"


def _placeholder(message: str, width: int = 420, height: int = 180) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
        f'<rect width="100%" height="100%" fill="{_PAPER}"/>'
        f'<text x="50%" y="50%" text-anchor="middle" font-family={quoteattr(_FONT)} '
        f'font-size="13" fill="#7c8b92">{escape(message)}</text></svg>'
    )


def _truncate(text: str, box_width: int) -> str:
    limit = max(4, box_width // 7)  # ~7px per character at font-size 11.5
    return text if len(text) <= limit else text[: limit - 1] + "…"


def view_to_svg(model: ArchimateModel, view: View | None = None) -> str:
    """Render one view (default: the first) as a standalone SVG document."""
    from archflow.drawio import LAYER_FILLS, layer_of  # local: keep archimate importable alone

    if view is None:
        view = model.views[0] if model.views else None
    if view is None or not view.nodes:
        return _placeholder("No view yet — the diagram appears after the first layout")

    elements = {element.id: element for element in model.elements}
    relationships = {rel.id: rel for rel in model.relationships}
    nodes = {node.id: node for node in view.nodes}

    pad = 24
    min_x = min(n.x for n in view.nodes) - pad
    min_y = min(n.y for n in view.nodes) - pad
    max_x = max(n.x + n.w for n in view.nodes) + pad
    max_y = max(n.y + n.h for n in view.nodes) + pad
    width, height = max_x - min_x, max_y - min_y

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{min_x} {min_y} {width} {height}" role="img" '
        f"aria-label={quoteattr(f'ArchiMate view: {view.name}')}>",
        f'<rect x="{min_x}" y="{min_y}" width="100%" height="100%" fill="{_PAPER}"/>',
        '<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L8,4 L0,8 z" fill="{_LINE}"/></marker></defs>',
    ]

    # Connections first, so element boxes paint over the line ends.
    for connection in view.connections:
        source = nodes.get(connection.source_node)
        target = nodes.get(connection.target_node)
        if source is None or target is None:
            continue
        x1, y1 = source.x + source.w / 2, source.y + source.h / 2
        x2, y2 = target.x + target.w / 2, target.y + target.h / 2
        rel = relationships.get(connection.relationship_ref)
        dashed = ' stroke-dasharray="5 3"' if rel and rel.type == "Realization" else ""
        parts.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{_LINE}" stroke-width="1.2" marker-end="url(#arrow)"{dashed}/>'
        )

    for node in view.nodes:
        element = elements.get(node.element_ref)
        if element is None:
            continue
        fill = LAYER_FILLS[layer_of(element.type)]
        cx = node.x + node.w / 2
        parts.append(
            f'<rect x="{node.x}" y="{node.y}" width="{node.w}" height="{node.h}" rx="3" '
            f'fill="{fill}" stroke="{_INK}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{cx:.0f}" y="{node.y + node.h / 2 + 1:.0f}" text-anchor="middle" '
            f'font-family={quoteattr(_FONT)} font-size="11.5" font-weight="600" '
            f'fill="{_INK}">{escape(_truncate(element.name, node.w))}</text>'
        )
        parts.append(
            f'<text x="{cx:.0f}" y="{node.y + node.h / 2 + 14:.0f}" text-anchor="middle" '
            f'font-family={quoteattr(_FONT)} font-size="8.5" '
            f'fill="{_LINE}">{escape(_truncate(element.type, node.w + 20))}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
