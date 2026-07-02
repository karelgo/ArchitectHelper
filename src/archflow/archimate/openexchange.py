"""ArchiMate Model Exchange File Format 3.0 (Open Exchange) reader/writer.

Serializes :class:`~archflow.archimate.model.ArchimateModel` instances to the
Open Group exchange schema and parses them back, using only the standard
library (:mod:`xml.etree.ElementTree`).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from archflow.archimate.model import (
    ArchimateModel,
    Element,
    Relationship,
    View,
    ViewConnection,
    ViewNode,
)

ARCHIMATE_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_XSI_TYPE = f"{{{XSI_NS}}}type"


def _q(tag: str) -> str:
    """Qualify a tag name with the ArchiMate exchange namespace."""
    return f"{{{ARCHIMATE_NS}}}{tag}"


def _write_id(raw: str) -> str:
    """Prefix a raw id so identifiers start with a letter, per the schema."""
    return f"id-{raw}"


def _read_id(identifier: str) -> str:
    """Strip the ``id-`` prefix added by :func:`_write_id`, if present."""
    return identifier.removeprefix("id-")


#: Characters outside the XML 1.0 legal range (ET emits them unescaped,
#: producing files that strict importers like Archi/Enterprise Studio reject).
_ILLEGAL_XML_CHARS = {
    c: None
    for c in range(0x20)
    if chr(c) not in ("\t", "\n", "\r")
}


def _clean_text(text: str) -> str:
    """Strip XML-1.0-illegal control characters from text content."""
    return text.translate(_ILLEGAL_XML_CHARS)


def _lang_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    """Append a child element carrying language-tagged text."""
    child = ET.SubElement(parent, _q(tag), {_XML_LANG: "en"})
    child.text = _clean_text(text)
    return child


def write_model(model: ArchimateModel) -> str:
    """Serialize a model to a pretty-printed Open Exchange 3.0 XML string.

    Child order follows the schema: name, documentation, elements,
    relationships, propertyDefinitions, views.
    """
    root = ET.Element(_q("model"), {"identifier": _write_id(model.id)})
    _lang_text(root, "name", model.name)
    if model.documentation:
        _lang_text(root, "documentation", model.documentation)

    # Property definitions are keyed by property name, in first-seen order.
    property_ids: dict[str, str] = {}
    for element in model.elements:
        for key in element.properties:
            if key not in property_ids:
                property_ids[key] = f"propid-{len(property_ids)}"

    if model.elements:
        elements_el = ET.SubElement(root, _q("elements"))
        for element in model.elements:
            el = ET.SubElement(
                elements_el,
                _q("element"),
                {"identifier": _write_id(element.id), _XSI_TYPE: element.type},
            )
            _lang_text(el, "name", element.name)
            if element.documentation:
                _lang_text(el, "documentation", element.documentation)
            if element.properties:
                props_el = ET.SubElement(el, _q("properties"))
                for key, value in element.properties.items():
                    prop_el = ET.SubElement(
                        props_el, _q("property"), {"propertyDefinitionRef": property_ids[key]}
                    )
                    _lang_text(prop_el, "value", value)

    if model.relationships:
        relationships_el = ET.SubElement(root, _q("relationships"))
        for relationship in model.relationships:
            rel_el = ET.SubElement(
                relationships_el,
                _q("relationship"),
                {
                    "identifier": _write_id(relationship.id),
                    "source": _write_id(relationship.source),
                    "target": _write_id(relationship.target),
                    _XSI_TYPE: relationship.type,
                },
            )
            if relationship.name:
                _lang_text(rel_el, "name", relationship.name)
            if relationship.documentation:
                _lang_text(rel_el, "documentation", relationship.documentation)

    if property_ids:
        defs_el = ET.SubElement(root, _q("propertyDefinitions"))
        for key, prop_id in property_ids.items():
            def_el = ET.SubElement(
                defs_el, _q("propertyDefinition"), {"identifier": prop_id, "type": "string"}
            )
            _lang_text(def_el, "name", key)

    if model.views:
        views_el = ET.SubElement(root, _q("views"))
        diagrams_el = ET.SubElement(views_el, _q("diagrams"))
        for view in model.views:
            view_el = ET.SubElement(
                diagrams_el,
                _q("view"),
                {"identifier": _write_id(view.id), _XSI_TYPE: "Diagram"},
            )
            _lang_text(view_el, "name", view.name)
            if view.documentation:
                _lang_text(view_el, "documentation", view.documentation)
            for node in view.nodes:
                ET.SubElement(
                    view_el,
                    _q("node"),
                    {
                        "identifier": _write_id(node.id),
                        "elementRef": _write_id(node.element_ref),
                        _XSI_TYPE: "Element",
                        "x": str(node.x),
                        "y": str(node.y),
                        "w": str(node.w),
                        "h": str(node.h),
                    },
                )
            for connection in view.connections:
                ET.SubElement(
                    view_el,
                    _q("connection"),
                    {
                        "identifier": _write_id(connection.id),
                        "relationshipRef": _write_id(connection.relationship_ref),
                        _XSI_TYPE: "Relationship",
                        "source": _write_id(connection.source_node),
                        "target": _write_id(connection.target_node),
                    },
                )

    ET.register_namespace("", ARCHIMATE_NS)
    ET.register_namespace("xsi", XSI_NS)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _text_of(parent: ET.Element, tag: str) -> str:
    """Text content of the first ``tag`` child, or empty string."""
    child = parent.find(_q(tag))
    return (child.text or "") if child is not None else ""


def _read_elements(root: ET.Element, property_names: dict[str, str]) -> list[Element]:
    elements: list[Element] = []
    container = root.find(_q("elements"))
    if container is None:
        return elements
    for el in container.findall(_q("element")):
        properties: dict[str, str] = {}
        props_el = el.find(_q("properties"))
        if props_el is not None:
            for prop_el in props_el.findall(_q("property")):
                ref = prop_el.get("propertyDefinitionRef", "")
                key = property_names.get(ref, ref)
                properties[key] = _text_of(prop_el, "value")
        elements.append(
            Element(
                id=_read_id(el.get("identifier", "")),
                type=el.get(_XSI_TYPE, ""),
                name=_text_of(el, "name"),
                documentation=_text_of(el, "documentation"),
                properties=properties,
            )
        )
    return elements


def _read_relationships(root: ET.Element) -> list[Relationship]:
    relationships: list[Relationship] = []
    container = root.find(_q("relationships"))
    if container is None:
        return relationships
    for rel_el in container.findall(_q("relationship")):
        relationships.append(
            Relationship(
                id=_read_id(rel_el.get("identifier", "")),
                type=rel_el.get(_XSI_TYPE, ""),
                source=_read_id(rel_el.get("source", "")),
                target=_read_id(rel_el.get("target", "")),
                name=_text_of(rel_el, "name"),
                documentation=_text_of(rel_el, "documentation"),
            )
        )
    return relationships


def _read_views(root: ET.Element) -> list[View]:
    views: list[View] = []
    views_el = root.find(_q("views"))
    diagrams_el = views_el.find(_q("diagrams")) if views_el is not None else None
    if diagrams_el is None:
        return views
    for view_el in diagrams_el.findall(_q("view")):
        # iter() walks all descendants: nested <node> children (visual
        # containment, as written by Archi/Enterprise Studio) are flattened
        # into the node list rather than silently dropped. Exchange-format
        # coordinates are treated as absolute canvas coordinates.
        nodes = [
            ViewNode(
                id=_read_id(node_el.get("identifier", "")),
                element_ref=_read_id(node_el.get("elementRef", "")),
                x=int(node_el.get("x", "0")),
                y=int(node_el.get("y", "0")),
                w=int(node_el.get("w", "0")),
                h=int(node_el.get("h", "0")),
            )
            for node_el in view_el.iter(_q("node"))
        ]
        connections = [
            ViewConnection(
                id=_read_id(conn_el.get("identifier", "")),
                relationship_ref=_read_id(conn_el.get("relationshipRef", "")),
                source_node=_read_id(conn_el.get("source", "")),
                target_node=_read_id(conn_el.get("target", "")),
            )
            for conn_el in view_el.iter(_q("connection"))
        ]
        views.append(
            View(
                id=_read_id(view_el.get("identifier", "")),
                name=_text_of(view_el, "name"),
                documentation=_text_of(view_el, "documentation"),
                nodes=nodes,
                connections=connections,
            )
        )
    return views


def read_model(xml: str) -> ArchimateModel:
    """Parse an Open Exchange 3.0 XML document into an :class:`ArchimateModel`.

    Missing optional sections (documentation, elements, relationships,
    propertyDefinitions, views) are tolerated.
    """
    root = ET.fromstring(xml)

    property_names: dict[str, str] = {}
    defs_el = root.find(_q("propertyDefinitions"))
    if defs_el is not None:
        for def_el in defs_el.findall(_q("propertyDefinition")):
            property_names[def_el.get("identifier", "")] = _text_of(def_el, "name")

    return ArchimateModel(
        id=_read_id(root.get("identifier", "")),
        name=_text_of(root, "name"),
        documentation=_text_of(root, "documentation"),
        elements=_read_elements(root, property_names),
        relationships=_read_relationships(root),
        views=_read_views(root),
    )
