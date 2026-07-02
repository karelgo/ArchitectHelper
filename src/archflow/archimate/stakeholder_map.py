"""Build an ArchiMate motivation view (stakeholder map) from a request.

Maps the stakeholder analysis captured on an
:class:`~archflow.domain.models.ArchitectureRequest` onto ArchiMate motivation
elements and lays them out in a single deterministic diagram view.
"""

from __future__ import annotations

from archflow.archimate.model import ArchimateModel, Element, ViewConnection, ViewNode
from archflow.domain.models import ArchitectureRequest, Stakeholder

#: Diagram column per element type (left to right); unknown types get their
#: own trailing column instead of silently sharing the last one.
_COLUMNS: dict[str, int] = {"Stakeholder": 0, "Driver": 1, "Assessment": 2, "Goal": 3}
_OVERFLOW_COLUMN = len(_COLUMNS)

_COLUMN_X_START = 40
_COLUMN_X_STEP = 260
_ROW_Y_START = 40
_ROW_Y_STEP = 90
_NODE_W = 200
_NODE_H = 60


def _key(name: str) -> str:
    """Normalization for name matching: whitespace- and case-insensitive."""
    return name.strip().casefold()


def build_stakeholder_map(request: ArchitectureRequest) -> ArchimateModel:
    """Build a stakeholder-map ArchiMate model for the given request.

    Stakeholders become ``Stakeholder`` elements, concerns and explicit
    drivers become (case-insensitively deduplicated) ``Driver`` elements,
    assessments become ``Assessment`` elements and goals (plus the request's
    business goal) become ``Goal`` elements, all wired with ``Association``
    and ``Influence`` relationships and placed on one four-column view.
    """
    model = ArchimateModel(name=request.title, documentation=request.description)

    # Every stakeholder gets its own element, even under duplicate names;
    # name-based lookups resolve to ALL matching stakeholders.
    stakeholder_pairs: list[tuple[Stakeholder, Element]] = []
    stakeholders_by_key: dict[str, list[Element]] = {}
    for stakeholder in request.stakeholders:
        element = model.add_element(
            "Stakeholder",
            stakeholder.name,
            documentation=stakeholder.role,
            properties={
                "influence": stakeholder.influence.value,
                "interest": stakeholder.interest.value,
                "attitude": stakeholder.attitude.value,
            },
        )
        stakeholder_pairs.append((stakeholder, element))
        stakeholders_by_key.setdefault(_key(stakeholder.name), []).append(element)

    seen_associations: set[tuple[str, str]] = set()

    def associate(source_id: str, target_id: str) -> None:
        """Add an Association relationship once per (source, target) pair."""
        if (source_id, target_id) in seen_associations:
            return
        seen_associations.add((source_id, target_id))
        model.add_relationship("Association", source_id, target_id)

    def associate_stakeholders(stakeholder_name: str, target_id: str) -> None:
        """Associate every stakeholder matching the name to the target."""
        for element in stakeholders_by_key.get(_key(stakeholder_name), []):
            associate(element.id, target_id)

    # Concerns -> Driver elements, deduplicated case-insensitively
    # (first-seen casing wins), associated from each holding stakeholder.
    drivers_by_key: dict[str, Element] = {}
    for stakeholder, stakeholder_el in stakeholder_pairs:
        for concern in stakeholder.concerns:
            key = _key(concern)
            driver_el = drivers_by_key.get(key)
            if driver_el is None:
                driver_el = model.add_element("Driver", concern.strip())
                drivers_by_key[key] = driver_el
            associate(stakeholder_el.id, driver_el.id)

    # Explicit drivers, deduplicated against concern-derived drivers.
    for driver in request.drivers:
        key = _key(driver.name)
        driver_el = drivers_by_key.get(key)
        if driver_el is None:
            driver_el = model.add_element("Driver", driver.name, documentation=driver.description)
            drivers_by_key[key] = driver_el
        for name in driver.stakeholder_names:
            associate_stakeholders(name, driver_el.id)

    assessment_elements: list[Element] = []
    for assessment in request.assessments:
        assessment_el = model.add_element(
            "Assessment", assessment.name, documentation=assessment.description
        )
        assessment_elements.append(assessment_el)
        driver_el = drivers_by_key.get(_key(assessment.driver_name))
        if assessment.driver_name and driver_el is not None:
            associate(assessment_el.id, driver_el.id)

    goals_by_key: dict[str, Element] = {}
    for goal in request.goals:
        goal_el = model.add_element("Goal", goal.name, documentation=goal.description)
        goals_by_key.setdefault(_key(goal.name), goal_el)
        for name in goal.stakeholder_names:
            associate_stakeholders(name, goal_el.id)

    business_goal_el: Element | None = None
    if request.business_goal:
        key = _key(request.business_goal)
        business_goal_el = goals_by_key.get(key)
        if business_goal_el is None:
            business_goal_el = model.add_element("Goal", request.business_goal)
            goals_by_key[key] = business_goal_el

    # Assessments influence the business goal when one exists; otherwise the
    # motivation chain still connects — they influence every explicit goal.
    influence_targets = (
        [business_goal_el] if business_goal_el is not None else list(goals_by_key.values())
    )
    for assessment_el in assessment_elements:
        for goal_el in influence_targets:
            model.add_relationship("Influence", assessment_el.id, goal_el.id)

    _layout_view(model, f"Stakeholder Map — {request.title}")
    return model


def _layout_view(model: ArchimateModel, view_name: str) -> None:
    """Add one view with a node per element and a connection per relationship."""
    view = model.add_view(view_name)
    node_id_by_element: dict[str, str] = {}
    rows_used = [0] * (_OVERFLOW_COLUMN + 1)
    for element in model.elements:
        column = _COLUMNS.get(element.type, _OVERFLOW_COLUMN)
        row = rows_used[column]
        rows_used[column] += 1
        node = ViewNode(
            element_ref=element.id,
            x=_COLUMN_X_START + column * _COLUMN_X_STEP,
            y=_ROW_Y_START + row * _ROW_Y_STEP,
            w=_NODE_W,
            h=_NODE_H,
        )
        view.nodes.append(node)
        node_id_by_element[element.id] = node.id
    for relationship in model.relationships:
        view.connections.append(
            ViewConnection(
                relationship_ref=relationship.id,
                source_node=node_id_by_element[relationship.source],
                target_node=node_id_by_element[relationship.target],
            )
        )
