"""Tests for the ArchiMate Open Exchange 3.0 reader/writer."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from archflow.archimate.model import ArchimateModel, ViewConnection, ViewNode
from archflow.archimate.openexchange import ARCHIMATE_NS, read_model, write_model

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w.-]*$")


@pytest.fixture()
def rich_model() -> ArchimateModel:
    model = ArchimateModel(name="Payments revamp", documentation="Model docs")
    cio = model.add_element(
        "Stakeholder",
        "CIO",
        documentation="Chief Information Officer",
        properties={"influence": "high", "attitude": "champion"},
    )
    cost = model.add_element("Driver", "Cost efficiency")
    goal = model.add_element("Goal", "Reduce run cost", properties={"influence": "low"})
    assoc = model.add_relationship("Association", cio.id, cost.id, name="concerned with")
    infl = model.add_relationship("Influence", cost.id, goal.id)
    view = model.add_view("Stakeholder Map")
    view.documentation = "View docs"
    node_by_element = {}
    for i, element in enumerate(model.elements):
        node = ViewNode(element_ref=element.id, x=40 + i * 260, y=40, w=200, h=60)
        view.nodes.append(node)
        node_by_element[element.id] = node.id
    for rel in (assoc, infl):
        view.connections.append(
            ViewConnection(
                relationship_ref=rel.id,
                source_node=node_by_element[rel.source],
                target_node=node_by_element[rel.target],
            )
        )
    return model


def test_write_model_emits_declaration_and_namespace(rich_model: ArchimateModel) -> None:
    xml = write_model(rich_model)
    assert xml.startswith("<?xml")
    assert "utf-8" in xml.splitlines()[0]
    root = ET.fromstring(xml)
    assert root.tag == f"{{{ARCHIMATE_NS}}}model"


def test_write_model_schema_element_order(rich_model: ArchimateModel) -> None:
    root = ET.fromstring(write_model(rich_model))
    local_names = [child.tag.removeprefix(f"{{{ARCHIMATE_NS}}}") for child in root]
    assert local_names == [
        "name",
        "documentation",
        "elements",
        "relationships",
        "propertyDefinitions",
        "views",
    ]


def test_identifiers_are_schema_valid(rich_model: ArchimateModel) -> None:
    root = ET.fromstring(write_model(rich_model))
    checked = 0
    for node in root.iter():
        for attr in ("identifier", "elementRef", "relationshipRef", "propertyDefinitionRef"):
            value = node.get(attr)
            if value is not None:
                assert IDENTIFIER_RE.match(value), f"invalid identifier: {value!r}"
                checked += 1
    assert checked > 10


def test_view_connections_reference_node_identifiers(rich_model: ArchimateModel) -> None:
    root = ET.fromstring(write_model(rich_model))
    ns = f"{{{ARCHIMATE_NS}}}"
    view = root.find(f"{ns}views/{ns}diagrams/{ns}view")
    assert view is not None
    node_ids = {n.get("identifier") for n in view.findall(f"{ns}node")}
    connections = view.findall(f"{ns}connection")
    assert len(connections) == 2
    for conn in connections:
        assert conn.get("source") in node_ids
        assert conn.get("target") in node_ids


def test_round_trip_preserves_everything(rich_model: ArchimateModel) -> None:
    restored = read_model(write_model(rich_model))
    assert restored.model_dump() == rich_model.model_dump()


def test_round_trip_preserves_properties_specifically(rich_model: ArchimateModel) -> None:
    restored = read_model(write_model(rich_model))
    cio = restored.element_by_name("CIO")
    assert cio is not None
    assert cio.properties == {"influence": "high", "attitude": "champion"}
    goal = restored.element_by_name("Reduce run cost")
    assert goal is not None
    assert goal.properties == {"influence": "low"}


def test_read_model_tolerates_missing_optional_sections() -> None:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<model xmlns="{ARCHIMATE_NS}" identifier="id-abc123">'
        '<name xml:lang="en">Bare model</name>'
        "</model>"
    )
    model = read_model(xml)
    assert model.id == "abc123"
    assert model.name == "Bare model"
    assert model.documentation == ""
    assert model.elements == []
    assert model.relationships == []
    assert model.views == []


def test_empty_model_round_trip() -> None:
    model = ArchimateModel(name="Empty")
    restored = read_model(write_model(model))
    assert restored.model_dump() == model.model_dump()
