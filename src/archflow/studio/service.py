"""Application service for Studio view projects.

Owns the lifecycle of :class:`ViewProject`: creation (blank, from an
architecture request's stakeholder map, or by importing an Open Exchange
file), draw.io round-trips, and exports. The copilot mutates projects through
the same service so every path keeps ``model_rev`` accounting honest.
"""

from __future__ import annotations

from archflow.archimate.model import ArchimateModel
from archflow.archimate.openexchange import read_model, write_model
from archflow.archimate.stakeholder_map import build_stakeholder_map
from archflow.domain.models import ArchitectureRequest
from archflow.drawio import apply_drawio_geometry, to_drawio_xml
from archflow.storage.repository import ViewProjectRepository
from archflow.studio.models import ViewProject


class StudioService:
    """CRUD + conversion operations for view projects."""

    def __init__(self, projects: ViewProjectRepository) -> None:
        self._projects = projects

    # -- lifecycle -------------------------------------------------------------

    def create(self, name: str, description: str = "") -> ViewProject:
        """Create an empty project (the copilot or the user fills it in)."""
        project = ViewProject(name=name, description=description, model=ArchimateModel(name=name))
        self._projects.save(project)
        return project

    def create_from_request(self, name: str, request: ArchitectureRequest) -> ViewProject:
        """Seed a project with the request's generated stakeholder map."""
        project = ViewProject(
            name=name or f"Stakeholder map — {request.title}",
            description=request.description,
            request_id=request.id,
            model=build_stakeholder_map(request),
        )
        project.bump_model()
        self._projects.save(project)
        return project

    def import_exchange(self, xml: str, name: str = "") -> ViewProject:
        """Create a project from an ArchiMate Open Exchange document."""
        model = read_model(xml)
        project = ViewProject(name=name or model.name or "Imported model", model=model)
        project.bump_model()
        self._projects.save(project)
        return project

    def get(self, project_id: str) -> ViewProject:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown view project id: {project_id}")
        return project

    def list(self) -> list[ViewProject]:
        return self._projects.list()

    def delete(self, project_id: str) -> None:
        self._projects.delete(project_id)

    def update_meta(self, project_id: str, name: str | None, description: str | None) -> ViewProject:
        project = self.get(project_id)
        if name:
            project.name = name
            project.model.name = name
        if description is not None:
            project.description = description
        project.touch()
        self._projects.save(project)
        return project

    def save(self, project: ViewProject) -> None:
        """Persist a project mutated elsewhere (e.g. by the copilot)."""
        self._projects.save(project)

    # -- draw.io round-trip ------------------------------------------------------

    def drawio_document(self, project_id: str) -> str:
        """The draw.io XML to load in the editor.

        Returns the stored document when it is current; otherwise re-renders
        from the canonical model (layout survives via the synced view) and
        caches the result.
        """
        project = self.get(project_id)
        if project.diagram_is_current:
            return project.drawio_xml
        xml = to_drawio_xml(project.model)
        project.drawio_xml = xml
        project.drawio_rev = project.model_rev
        self._projects.save(project)
        return xml

    def save_drawio_document(self, project_id: str, xml: str) -> int:
        """Store an edited draw.io document and sync geometry into the model."""
        project = self.get(project_id)
        changed = apply_drawio_geometry(project.model, xml)
        project.drawio_xml = xml
        project.drawio_rev = project.model_rev
        project.touch()
        self._projects.save(project)
        return changed

    # -- export --------------------------------------------------------------------

    def exchange_document(self, project_id: str) -> str:
        """The project's model as ArchiMate Open Exchange XML."""
        return write_model(self.get(project_id).model)
