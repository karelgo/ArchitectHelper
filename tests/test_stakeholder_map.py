"""Tests for building an ArchiMate stakeholder map from a request."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from archflow.archimate.openexchange import ARCHIMATE_NS, read_model
from archflow.archimate.stakeholder_map import build_stakeholder_map
from archflow.domain.models import (
    ArchitectureRequest,
    Assessment,
    Attitude,
    Driver,
    Goal,
    InfluenceLevel,
    Stakeholder,
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w.-]*$")


@pytest.fixture()
def request_fixture() -> ArchitectureRequest:
    return ArchitectureRequest(
        title="Payments platform revamp",
        description="Replace the legacy payments engine.",
        business_goal="Improve compliance posture",
        stakeholders=[
            Stakeholder(
                name="Alice",
                role="CIO",
                concerns=["Cost efficiency", "Security"],
                influence=InfluenceLevel.HIGH,
                interest=InfluenceLevel.HIGH,
                attitude=Attitude.CHAMPION,
            ),
            Stakeholder(
                name="Bob",
                role="CISO",
                concerns=["security"],  # same concern as Alice, different casing
                influence=InfluenceLevel.MEDIUM,
                interest=InfluenceLevel.HIGH,
                attitude=Attitude.CRITICAL,
            ),
            Stakeholder(
                name="Carol",
                role="Head of Operations",
                concerns=["SECURITY", "Vendor lock-in"],
                attitude=Attitude.NEUTRAL,
            ),
        ],
        drivers=[
            Driver(
                name="Regulatory pressure",
                description="PSD3 is coming.",
                stakeholder_names=["Bob"],
            ),
            # Duplicates the shared concern (case-insensitively): no new element.
            Driver(name="SECURITY", stakeholder_names=["Alice"]),
        ],
        assessments=[
            Assessment(
                name="Audit finding: weak IAM",
                description="2025 audit flagged IAM gaps.",
                driver_name="security",
            )
        ],
        goals=[
            Goal(name="Reduce run cost", stakeholder_names=["Alice"]),
        ],
    )


def test_model_metadata_from_request(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    assert model.name == "Payments platform revamp"
    assert model.documentation == "Replace the legacy payments engine."


def test_element_counts_and_dedupe(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    by_type: dict[str, list[str]] = {}
    for element in model.elements:
        by_type.setdefault(element.type, []).append(element.name)

    assert by_type["Stakeholder"] == ["Alice", "Bob", "Carol"]
    # Concern drivers keep first-seen casing; "SECURITY" driver deduped away.
    assert by_type["Driver"] == ["Cost efficiency", "Security", "Vendor lock-in", "Regulatory pressure"]
    assert by_type["Assessment"] == ["Audit finding: weak IAM"]
    assert by_type["Goal"] == ["Reduce run cost", "Improve compliance posture"]
    assert len(model.elements) == 10


def test_stakeholder_properties_and_documentation(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    alice = model.element_by_name("Alice")
    assert alice is not None
    assert alice.documentation == "CIO"
    assert alice.properties == {"influence": "high", "interest": "high", "attitude": "champion"}


def test_association_endpoints(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    name_by_id = {element.id: element.name for element in model.elements}
    associations = {
        (name_by_id[rel.source], name_by_id[rel.target])
        for rel in model.relationships
        if rel.type == "Association"
    }
    assert associations == {
        ("Alice", "Cost efficiency"),
        ("Alice", "Security"),
        ("Bob", "Security"),
        ("Carol", "Security"),
        ("Carol", "Vendor lock-in"),
        ("Bob", "Regulatory pressure"),
        ("Audit finding: weak IAM", "Security"),
        ("Alice", "Reduce run cost"),
    }


def test_influence_from_assessment_to_business_goal(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    name_by_id = {element.id: element.name for element in model.elements}
    influences = [
        (name_by_id[rel.source], name_by_id[rel.target])
        for rel in model.relationships
        if rel.type == "Influence"
    ]
    assert influences == [("Audit finding: weak IAM", "Improve compliance posture")]
    assert len(model.relationships) == 9


def test_single_view_covers_all_elements_and_relationships(
    request_fixture: ArchitectureRequest,
) -> None:
    model = build_stakeholder_map(request_fixture)
    assert len(model.views) == 1
    view = model.views[0]
    assert view.name == "Stakeholder Map — Payments platform revamp"

    assert {node.element_ref for node in view.nodes} == {e.id for e in model.elements}
    assert len(view.nodes) == len(model.elements)
    assert {c.relationship_ref for c in view.connections} == {r.id for r in model.relationships}
    assert len(view.connections) == len(model.relationships)

    node_ids = {node.id for node in view.nodes}
    for connection in view.connections:
        assert connection.source_node in node_ids
        assert connection.target_node in node_ids


def test_view_layout_columns_and_rows(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    view = model.views[0]
    element_by_id = {element.id: element for element in model.elements}
    column_x = {"Stakeholder": 40, "Driver": 300, "Assessment": 560, "Goal": 820}

    rows_seen: dict[int, list[int]] = {}
    for node in view.nodes:
        element = element_by_id[node.element_ref]
        assert node.x == column_x[element.type]
        assert (node.w, node.h) == (200, 60)
        rows_seen.setdefault(node.x, []).append(node.y)

    for ys in rows_seen.values():
        assert ys == [40 + i * 90 for i in range(len(ys))]


def test_business_goal_not_duplicated_when_listed_as_goal() -> None:
    request = ArchitectureRequest(
        title="T",
        business_goal="improve compliance posture",
        goals=[Goal(name="Improve compliance posture")],
        assessments=[Assessment(name="A1")],
    )
    model = build_stakeholder_map(request)
    goals = [e for e in model.elements if e.type == "Goal"]
    assert [g.name for g in goals] == ["Improve compliance posture"]
    influences = [r for r in model.relationships if r.type == "Influence"]
    assert len(influences) == 1
    assert influences[0].target == goals[0].id


def test_stakeholder_map_xml_round_trip(request_fixture: ArchitectureRequest) -> None:
    model = build_stakeholder_map(request_fixture)
    xml = model.to_open_exchange_xml()

    root = ET.fromstring(xml)
    assert root.tag == f"{{{ARCHIMATE_NS}}}model"
    for node in root.iter():
        identifier = node.get("identifier")
        if identifier is not None:
            assert IDENTIFIER_RE.match(identifier)

    restored = read_model(xml)
    assert restored.model_dump() == model.model_dump()


def test_duplicate_stakeholder_names_each_get_their_own_links() -> None:
    """Two same-named stakeholders both keep their concerns wired."""
    from archflow.domain.models import ArchitectureRequest, Stakeholder

    request = ArchitectureRequest(
        title="Dup",
        stakeholders=[
            Stakeholder(name="Alice", role="Sales", concerns=["Adoption"]),
            Stakeholder(name="Alice", role="Finance", concerns=["Budget"]),
        ],
    )
    model = build_stakeholder_map(request)
    stakeholder_els = [e for e in model.elements if e.type == "Stakeholder"]
    assert len(stakeholder_els) == 2
    linked_sources = {r.source for r in model.relationships if r.type == "Association"}
    assert {e.id for e in stakeholder_els} <= linked_sources, "no orphaned duplicate"


def test_name_matching_tolerates_whitespace() -> None:
    from archflow.domain.models import ArchitectureRequest, Driver, Stakeholder

    request = ArchitectureRequest(
        title="WS",
        stakeholders=[Stakeholder(name="Alice ", concerns=[])],
        drivers=[Driver(name="Cost", stakeholder_names=[" alice"])],
    )
    model = build_stakeholder_map(request)
    associations = [r for r in model.relationships if r.type == "Association"]
    assert len(associations) == 1, "whitespace must not break stakeholder matching"


def test_assessments_influence_goals_without_business_goal() -> None:
    from archflow.domain.models import ArchitectureRequest, Assessment, Goal, Stakeholder

    request = ArchitectureRequest(
        title="NoBG",
        business_goal="",
        stakeholders=[Stakeholder(name="A", concerns=["c"])],
        goals=[Goal(name="Reduce cost")],
        assessments=[Assessment(name="Legacy EOL", driver_name="c")],
    )
    model = build_stakeholder_map(request)
    influences = [r for r in model.relationships if r.type == "Influence"]
    assert influences, "assessment must stay connected to the motivation chain"
