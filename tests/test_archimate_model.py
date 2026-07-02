"""Tests for the in-memory ArchiMate model."""

from __future__ import annotations

import pytest

from archflow.archimate.model import (
    ELEMENT_TYPES,
    RELATIONSHIP_TYPES,
    ArchimateModel,
)


def _model() -> ArchimateModel:
    return ArchimateModel(name="Test model")


def test_type_catalogues_contain_expected_entries() -> None:
    assert {"Stakeholder", "Driver", "Assessment", "Goal", "ApplicationComponent"} <= ELEMENT_TYPES
    assert {"Association", "Influence", "Serving", "Junction"} <= RELATIONSHIP_TYPES


def test_add_element_registers_element_with_defaults() -> None:
    model = _model()
    element = model.add_element("Stakeholder", "CIO", documentation="Chief Information Officer")
    assert element in model.elements
    assert element.type == "Stakeholder"
    assert element.name == "CIO"
    assert element.documentation == "Chief Information Officer"
    assert element.properties == {}
    assert element.id  # auto-generated


def test_add_element_with_properties() -> None:
    model = _model()
    element = model.add_element("Driver", "Cost", properties={"weight": "high"})
    assert element.properties == {"weight": "high"}


def test_add_element_unknown_type_raises() -> None:
    model = _model()
    with pytest.raises(ValueError, match="element type"):
        model.add_element("Banana", "Nope")
    assert model.elements == []


def test_add_relationship_between_registered_elements() -> None:
    model = _model()
    a = model.add_element("Stakeholder", "CIO")
    b = model.add_element("Driver", "Cost")
    rel = model.add_relationship("Association", a.id, b.id, name="cares about")
    assert rel in model.relationships
    assert (rel.source, rel.target, rel.type, rel.name) == (a.id, b.id, "Association", "cares about")


def test_add_relationship_unknown_type_raises() -> None:
    model = _model()
    a = model.add_element("Stakeholder", "CIO")
    b = model.add_element("Driver", "Cost")
    with pytest.raises(ValueError, match="relationship type"):
        model.add_relationship("Likes", a.id, b.id)


def test_add_relationship_unknown_endpoints_raise() -> None:
    model = _model()
    a = model.add_element("Stakeholder", "CIO")
    with pytest.raises(ValueError, match="source"):
        model.add_relationship("Association", "missing", a.id)
    with pytest.raises(ValueError, match="target"):
        model.add_relationship("Association", a.id, "missing")
    assert model.relationships == []


def test_add_view_and_element_by_name() -> None:
    model = _model()
    view = model.add_view("Overview")
    assert view in model.views
    assert view.name == "Overview"
    assert view.nodes == [] and view.connections == []

    element = model.add_element("Goal", "Reduce cost")
    assert model.element_by_name("Reduce cost") is element
    assert model.element_by_name("Unknown") is None


def test_to_and_from_open_exchange_xml_delegate() -> None:
    model = _model()
    model.add_element("Stakeholder", "CIO")
    xml = model.to_open_exchange_xml()
    assert xml.startswith("<?xml")
    restored = ArchimateModel.from_open_exchange_xml(xml)
    assert restored.model_dump() == model.model_dump()
