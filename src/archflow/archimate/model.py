"""In-memory ArchiMate 3.x model: elements, relationships and diagram views.

The model is deliberately small: just enough structure to build motivation
views (stakeholder maps) and serialize them to the ArchiMate Model Exchange
File Format 3.0 (see :mod:`archflow.archimate.openexchange`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archflow.domain.models import new_id

#: ArchiMate 3.x element type names accepted by :meth:`ArchimateModel.add_element`.
ELEMENT_TYPES: frozenset[str] = frozenset(
    {
        # Motivation layer
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
        # Business layer
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
        # Application layer
        "ApplicationComponent",
        "ApplicationCollaboration",
        "ApplicationInterface",
        "ApplicationFunction",
        "ApplicationInteraction",
        "ApplicationProcess",
        "ApplicationEvent",
        "ApplicationService",
        "DataObject",
        # Technology layer (core)
        "Node",
        "Device",
        "SystemSoftware",
        "TechnologyService",
        "Artifact",
        # Physical / composite / strategy / implementation & migration
        "Location",
        "Grouping",
        "WorkPackage",
        "Deliverable",
        "Plateau",
        "Gap",
        "Capability",
        "Resource",
        "CourseOfAction",
        "ValueStream",
        # Junctions are element types in the exchange format, not relationships
        "AndJunction",
        "OrJunction",
    }
)

#: ArchiMate 3.x relationship type names accepted by :meth:`ArchimateModel.add_relationship`.
RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {
        "Composition",
        "Aggregation",
        "Assignment",
        "Realization",
        "Serving",
        "Access",
        "Influence",
        "Triggering",
        "Flow",
        "Specialization",
        "Association",
    }
)


class Element(BaseModel):
    """A single ArchiMate element (concept) in the model."""

    id: str = Field(default_factory=new_id)
    type: str
    name: str
    documentation: str = ""
    properties: dict[str, str] = Field(default_factory=dict)


class Relationship(BaseModel):
    """A typed relationship between two elements (by element id)."""

    id: str = Field(default_factory=new_id)
    type: str
    source: str
    target: str
    name: str = ""


class ViewNode(BaseModel):
    """Placement of one element on a diagram view."""

    id: str = Field(default_factory=new_id)
    element_ref: str
    x: int = 0
    y: int = 0
    w: int = 120
    h: int = 55


class ViewConnection(BaseModel):
    """Rendering of one relationship on a diagram view, between two nodes."""

    id: str = Field(default_factory=new_id)
    relationship_ref: str
    source_node: str
    target_node: str


class View(BaseModel):
    """A diagram view: a named set of nodes and connections."""

    id: str = Field(default_factory=new_id)
    name: str
    nodes: list[ViewNode] = Field(default_factory=list)
    connections: list[ViewConnection] = Field(default_factory=list)
    documentation: str = ""


class ArchimateModel(BaseModel):
    """An in-memory ArchiMate model with convenience builder methods."""

    id: str = Field(default_factory=new_id)
    name: str
    documentation: str = ""
    elements: list[Element] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)

    def add_element(
        self,
        type: str,
        name: str,
        documentation: str = "",
        properties: dict[str, str] | None = None,
    ) -> Element:
        """Create, register and return a new element.

        Raises:
            ValueError: If ``type`` is not a known ArchiMate element type.
        """
        if type not in ELEMENT_TYPES:
            raise ValueError(f"Unknown ArchiMate element type: {type!r}")
        element = Element(
            type=type, name=name, documentation=documentation, properties=properties or {}
        )
        self.elements.append(element)
        return element

    def add_relationship(
        self, type: str, source: str, target: str, name: str = ""
    ) -> Relationship:
        """Create, register and return a new relationship between two element ids.

        Raises:
            ValueError: If ``type`` is unknown or either endpoint id does not
                refer to an element in this model.
        """
        if type not in RELATIONSHIP_TYPES:
            raise ValueError(f"Unknown ArchiMate relationship type: {type!r}")
        known_ids = {element.id for element in self.elements}
        if source not in known_ids:
            raise ValueError(f"Relationship source is not an element in this model: {source!r}")
        if target not in known_ids:
            raise ValueError(f"Relationship target is not an element in this model: {target!r}")
        relationship = Relationship(type=type, source=source, target=target, name=name)
        self.relationships.append(relationship)
        return relationship

    def add_view(self, name: str) -> View:
        """Create, register and return a new (empty) diagram view."""
        view = View(name=name)
        self.views.append(view)
        return view

    def element_by_name(self, name: str) -> Element | None:
        """First element with the given name, or ``None``."""
        for element in self.elements:
            if element.name == name:
                return element
        return None

    def to_open_exchange_xml(self) -> str:
        """Serialize this model to ArchiMate Open Exchange 3.0 XML."""
        from archflow.archimate import openexchange

        return openexchange.write_model(self)

    @classmethod
    def from_open_exchange_xml(cls, xml: str) -> ArchimateModel:
        """Parse an ArchiMate Open Exchange 3.0 XML document into a model."""
        from archflow.archimate import openexchange

        return openexchange.read_model(xml)
