"""Build an ArchiMate motivation view (stakeholder map) from a request.

Maps the stakeholder analysis captured on an
:class:`~archflow.domain.models.ArchitectureRequest` onto ArchiMate motivation
elements and lays them out in a single deterministic diagram view.
"""

from __future__ import annotations

from archflow.archimate.model import ArchimateModel, Element, ViewConnection, ViewNode
from archflow.domain.models import ArchitectureRequest

#: Diagram column per element type (left to right).
_COLUMNS: dict[str, int] = {"Stakeholder": 0, "Driver": 1, "Assessment": 2, "Goal": 3}

_COLUMN_X_START = 40
_COLUMN_X_STEP = 260
_ROW_Y_START = 40
_ROW_Y_STEP = 90
_NODE_W = 200
_NODE_H = 60


def build_stakeholder_map(request: ArchitectureRequest) -> ArchimateModel:
    """Build a stakeholder-map ArchiMate model for the given request.

    Stakeholders become ``Stakeholder`` elements, concerns and explicit
    drivers become (case-insensitively deduplicated) ``Driver`` elements,
    assessments become ``Assessment`` elements and goals (plus the request's
    business goal) become ``Goal`` elements, all wired with ``Association``
    and ``Influence`` relationships and placed on one four-column view.
    """
    model = ArchimateModel(name=request.title, documentation=request.description)

    stakeholders_by_key: dict[str, Element] = {}
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
        stakeholders_by_key.setdefault(stakeholder.name.casefold(), element)

    seen_associations: set[tuple[str, str]] = set()

    def associate(source_id: str, target_id: str) -> None:
        """Add an Association relationship once per (source, target) pair."""
        if (source_id, target_id) in seen_associations:
            return
        seen_associations.add((source_id, target_id))
        model.add_relationship("Association", source_id, target_id)

    def associate_stakeholder(stakeholder_name: str, target_id: str) -> None:
        """Associate a stakeholder (looked up by name, case-insensitively) to a target."""
        found = stakeholders_by_key.get(stakeholder_name.casefold())
        if found is not None:
            associate(found.id, target_id)

    # Concerns -> Driver elements, deduplicated case-insensitively
    # (first-seen casing wins), associated from each holding stakeholder.
    drivers_by_key: dict[str, Element] = {}
    for stakeholder in request.stakeholders:
        stakeholder_el = stakeholders_by_key[stakeholder.name.casefold()]
        for concern in stakeholder.concerns:
            key = concern.casefold()
            driver_el = drivers_by_key.get(key)
            if driver_el is None:
                driver_el = model.add_element("Driver", concern)
                drivers_by_key[key] = driver_el
            associate(stakeholder_el.id, driver_el.id)

    # Explicit drivers, deduplicated against concern-derived drivers.
    for driver in request.drivers:
        key = driver.name.casefold()
        driver_el = drivers_by_key.get(key)
        if driver_el is None:
            driver_el = model.add_element("Driver", driver.name, documentation=driver.description)
            drivers_by_key[key] = driver_el
        for name in driver.stakeholder_names:
            associate_stakeholder(name, driver_el.id)

    assessment_elements: list[Element] = []
    for assessment in request.assessments:
        assessment_el = model.add_element(
            "Assessment", assessment.name, documentation=assessment.description
        )
        assessment_elements.append(assessment_el)
        driver_el = drivers_by_key.get(assessment.driver_name.casefold())
        if assessment.driver_name and driver_el is not None:
            associate(assessment_el.id, driver_el.id)

    goals_by_key: dict[str, Element] = {}
    for goal in request.goals:
        goal_el = model.add_element("Goal", goal.name, documentation=goal.description)
        goals_by_key.setdefault(goal.name.casefold(), goal_el)
        for name in goal.stakeholder_names:
            associate_stakeholder(name, goal_el.id)

    business_goal_el: Element | None = None
    if request.business_goal:
        key = request.business_goal.casefold()
        business_goal_el = goals_by_key.get(key)
        if business_goal_el is None:
            business_goal_el = model.add_element("Goal", request.business_goal)
            goals_by_key[key] = business_goal_el

    if business_goal_el is not None:
        for assessment_el in assessment_elements:
            model.add_relationship("Influence", assessment_el.id, business_goal_el.id)

    _layout_view(model, f"Stakeholder Map — {request.title}")
    return model


def _layout_view(model: ArchimateModel, view_name: str) -> None:
    """Add one view with a node per element and a connection per relationship."""
    view = model.add_view(view_name)
    node_id_by_element: dict[str, str] = {}
    rows_used = [0, 0, 0, 0]
    for element in model.elements:
        column = _COLUMNS.get(element.type, len(rows_used) - 1)
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
