"""Tests for the ArchiMate model linter."""

from __future__ import annotations

from archflow.archimate.lint import Severity, lint_model, summarize
from archflow.archimate.model import ArchimateModel, Relationship, ViewConnection, ViewNode


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def make_clean_model() -> ArchimateModel:
    model = ArchimateModel(name="Clean")
    component = model.add_element("ApplicationComponent", "CRM")
    service = model.add_element("ApplicationService", "Customer data service")
    process = model.add_element("BusinessProcess", "Handle order")
    goal = model.add_element("Goal", "Happy customers")
    model.add_relationship("Realization", component.id, service.id)
    model.add_relationship("Serving", service.id, process.id)
    model.add_relationship("Influence", process.id, goal.id)
    return model


def test_clean_model_has_no_findings() -> None:
    assert lint_model(make_clean_model()) == []


def test_influence_must_target_motivation() -> None:
    model = ArchimateModel(name="M")
    a = model.add_element("Goal", "G")
    b = model.add_element("ApplicationComponent", "C")
    model.add_relationship("Influence", a.id, b.id)
    findings = lint_model(model)
    assert "influence-target" in rules(findings)
    assert any(f.severity == Severity.ERROR for f in findings)


def test_access_must_target_passive_structure() -> None:
    model = ArchimateModel(name="M")
    process = model.add_element("BusinessProcess", "P")
    actor = model.add_element("BusinessActor", "A")
    data = model.add_element("DataObject", "D")
    model.add_relationship("Access", process.id, actor.id)  # bad target
    model.add_relationship("Access", process.id, data.id)  # fine
    findings = lint_model(model)
    assert "access-target" in rules(findings)
    bad = [f for f in findings if f.rule == "access-target"]
    assert len(bad) == 1


def test_stakeholder_cannot_be_realized() -> None:
    model = ArchimateModel(name="M")
    service = model.add_element("BusinessService", "S")
    stakeholder = model.add_element("Stakeholder", "CIO")
    model.add_relationship("Realization", service.id, stakeholder.id)
    assert "unrealizable-target" in rules(lint_model(model))


def test_flow_between_passive_elements_warns() -> None:
    model = ArchimateModel(name="M")
    a = model.add_element("DataObject", "A")
    b = model.add_element("BusinessProcess", "B")
    model.add_relationship("Flow", a.id, b.id)
    findings = [f for f in lint_model(model) if f.rule == "flow-endpoints"]
    assert findings and findings[0].severity == Severity.WARNING


def test_specialization_between_different_types_warns() -> None:
    model = ArchimateModel(name="M")
    a = model.add_element("BusinessProcess", "P")
    b = model.add_element("ApplicationService", "S")
    model.add_relationship("Specialization", a.id, b.id)
    assert "specialization-type" in rules(lint_model(model))


def test_cross_layer_composition_warns_but_grouping_exempt() -> None:
    model = ArchimateModel(name="M")
    process = model.add_element("BusinessProcess", "P")
    component = model.add_element("ApplicationComponent", "C")
    group = model.add_element("Grouping", "Zone")
    model.add_relationship("Composition", process.id, component.id)  # cross-layer
    model.add_relationship("Composition", group.id, component.id)  # grouping: fine
    findings = [f for f in lint_model(model) if f.rule == "cross-layer-structure"]
    assert len(findings) == 1


def test_structural_rules() -> None:
    model = ArchimateModel(name="M")
    a = model.add_element("Goal", "Same")
    model.add_element("Goal", "same")  # duplicate (case-insensitive)
    model.add_element("Driver", "")  # empty name
    lonely = model.add_element("Capability", "Lonely")
    model.add_relationship("Influence", lonely.id, a.id)
    # dangling relationship, injected past validation
    model.relationships.append(
        Relationship(type="Serving", source=a.id, target="ghost")
    )
    view = model.add_view("V")
    view.nodes.append(ViewNode(element_ref="ghost-el", x=0, y=0, w=10, h=10))
    view.connections.append(
        ViewConnection(relationship_ref="ghost-rel", source_node="n1", target_node="n2")
    )

    found = rules(lint_model(model))
    assert {
        "duplicate-name",
        "empty-name",
        "dangling-relationship",
        "dangling-view-node",
        "dangling-view-connection",
        "not-on-view",
    } <= found


def test_orphan_detection_and_summary() -> None:
    model = ArchimateModel(name="M")
    model.add_element("Goal", "A")
    model.add_element("Goal", "B")
    findings = lint_model(model)
    assert [f for f in findings if f.rule == "orphan-element"]
    assert "warning" in summarize(findings)


def test_findings_sorted_errors_first() -> None:
    model = ArchimateModel(name="M")
    a = model.add_element("Goal", "A")  # orphan-ish once more elements exist
    b = model.add_element("ApplicationComponent", "B")
    model.add_relationship("Influence", b.id, b.id)  # wait, self influence to component: error
    model.add_relationship("Influence", a.id, b.id)
    findings = lint_model(model)
    severities = [f.severity for f in findings]
    assert severities == sorted(
        severities, key=lambda s: {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}[s]
    )
    assert findings[0].severity == Severity.ERROR
