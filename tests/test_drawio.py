"""Tests for the ArchiMate ⇄ draw.io converter."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from archflow.archimate.model import ArchimateModel, ViewNode
from archflow.drawio import apply_drawio_geometry, layer_of, to_drawio_xml


def make_model() -> ArchimateModel:
    model = ArchimateModel(name="Landscape")
    crm = model.add_element("ApplicationComponent", "CRM")
    sales = model.add_element("BusinessProcess", "Sales")
    goal = model.add_element("Goal", "Happy customers")
    model.add_relationship("Serving", crm.id, sales.id)
    model.add_relationship("Realization", sales.id, goal.id, name="realizes")
    return model


def cells_by_id(xml: str) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    return {c.get("id", ""): c for c in root.iter("mxCell")}


def test_layer_classification() -> None:
    assert layer_of("Stakeholder") == "motivation"
    assert layer_of("BusinessActor") == "business"
    assert layer_of("ApplicationComponent") == "application"
    assert layer_of("Node") == "technology"
    assert layer_of("Capability") == "strategy"
    assert layer_of("Grouping") == "other"


def test_to_drawio_produces_cell_per_element_and_edge() -> None:
    model = make_model()
    xml = to_drawio_xml(model)
    cells = cells_by_id(xml)

    for element in model.elements:
        cell = cells[f"el-{element.id}"]
        assert cell.get("vertex") == "1"
        assert element.name in cell.get("value", "")
        assert element.type in cell.get("value", "")
        assert cell.find("mxGeometry") is not None

    for relationship in model.relationships:
        cell = cells[f"rel-{relationship.id}"]
        assert cell.get("edge") == "1"
        assert cell.get("source") == f"el-{relationship.source}"
        assert cell.get("target") == f"el-{relationship.target}"


def test_layer_fills_and_edge_notation_styles() -> None:
    model = make_model()
    cells = cells_by_id(to_drawio_xml(model))
    styles = {
        element.name: cells[f"el-{element.id}"].get("style", "") for element in model.elements
    }
    assert "fillColor=#99FFFF" in styles["CRM"]  # application blue
    assert "fillColor=#FFFF99" in styles["Sales"]  # business yellow
    assert "fillColor=#CCCCFF" in styles["Happy customers"]  # motivation purple

    edge_styles = [
        cells[f"rel-{r.id}"].get("style", "") for r in model.relationships
    ]
    serving, realization = edge_styles
    assert "endArrow=open" in serving
    assert "dashed=1" in realization and "endFill=0" in realization


def test_view_geometry_is_used_when_present() -> None:
    model = make_model()
    view = model.add_view("Custom")
    first = model.elements[0]
    view.nodes.append(ViewNode(element_ref=first.id, x=555, y=77, w=210, h=70))
    cells = cells_by_id(to_drawio_xml(model, view=view))
    geo = cells[f"el-{first.id}"].find("mxGeometry")
    assert geo is not None
    assert (geo.get("x"), geo.get("y")) == ("555", "77")
    # Elements missing from the view still get placed.
    assert cells[f"el-{model.elements[1].id}"].find("mxGeometry") is not None


def test_auto_layout_bands_do_not_overlap() -> None:
    model = ArchimateModel(name="Bands")
    for i in range(6):
        model.add_element("Goal", f"G{i}")
    for i in range(6):
        model.add_element("ApplicationComponent", f"A{i}")
    cells = cells_by_id(to_drawio_xml(model))
    goal_bottoms = []
    app_tops = []
    for element in model.elements:
        geo = cells[f"el-{element.id}"].find("mxGeometry")
        assert geo is not None
        y = int(geo.get("y", "0"))
        if element.type == "Goal":
            goal_bottoms.append(y + int(geo.get("height", "0")))
        else:
            app_tops.append(y)
    assert max(goal_bottoms) < min(app_tops)


def test_geometry_round_trip_updates_view() -> None:
    model = make_model()
    xml = to_drawio_xml(model)
    moved = xml.replace('x="40"', 'x="400"', 1)
    changed = apply_drawio_geometry(model, moved)
    assert changed >= 1
    assert model.views, "a view is created to hold the layout"
    xs = {node.x for node in model.views[0].nodes}
    assert 400 in xs


def test_apply_geometry_ignores_foreign_cells() -> None:
    model = make_model()
    xml = to_drawio_xml(model)
    # Simulate a user-drawn annotation box in draw.io.
    injected = xml.replace(
        "</root>",
        '<mxCell id="user-note" value="note" vertex="1" parent="1">'
        '<mxGeometry x="1" y="1" width="10" height="10" as="geometry"/></mxCell></root>',
    )
    changed = apply_drawio_geometry(model, injected)
    refs = {node.element_ref for node in model.views[0].nodes}
    assert "user-note" not in refs
    assert changed == len(model.elements)


def test_apply_geometry_empty_diagram_is_noop() -> None:
    model = make_model()
    assert apply_drawio_geometry(model, "<mxfile><diagram/></mxfile>") == 0
    assert not model.views


def test_fractional_geometry_from_drawio_is_tolerated() -> None:
    model = make_model()
    xml = to_drawio_xml(model).replace('x="40"', 'x="40.5"', 1)
    assert apply_drawio_geometry(model, xml) >= 1


def test_html_in_names_is_escaped() -> None:
    model = ArchimateModel(name="Esc")
    model.add_element("Goal", "<script>alert(1)</script> & co")
    xml = to_drawio_xml(model)
    assert "<script>" not in xml
    parsed = ET.fromstring(xml)  # must stay well-formed
    assert parsed is not None


def test_unknown_relationship_type_gets_default_edge() -> None:
    model = make_model()
    # Force an exotic type past validation to prove the style fallback.
    model.relationships[0].type = "SomethingNew"
    xml = to_drawio_xml(model)
    cells = cells_by_id(xml)
    assert "endArrow=open" in cells[f"rel-{model.relationships[0].id}"].get("style", "")


def test_dangling_relationship_is_skipped() -> None:
    model = make_model()
    model.relationships[0].target = "not-an-element"
    cells = cells_by_id(to_drawio_xml(model))
    assert f"rel-{model.relationships[0].id}" not in cells


@pytest.mark.parametrize("bad", ["", "<not-xml", "<mxfile>"])
def test_apply_geometry_rejects_bad_xml(bad: str) -> None:
    model = make_model()
    if bad in ("", "<not-xml"):
        with pytest.raises(ET.ParseError):
            apply_drawio_geometry(model, bad)
    else:
        with pytest.raises(ET.ParseError):
            apply_drawio_geometry(model, bad)
