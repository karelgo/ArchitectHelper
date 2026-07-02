"""Repositories persisting requests, events and view projects as JSON documents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError

from archflow.domain.events import Event
from archflow.domain.models import ArchitectureRequest
from archflow.storage.db import events_table, init_db, requests_table, view_projects_table

if TYPE_CHECKING:
    from archflow.studio.models import ViewProject


class RequestRepository:
    """CRUD store for :class:`ArchitectureRequest` aggregates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        init_db(engine)

    def save(self, request: ArchitectureRequest) -> None:
        """Insert or update the request (upsert keyed on ``request.id``)."""
        values = {
            "title": request.title,
            "stage": request.stage.value,
            "data": request.model_dump_json(),
            "updated_at": request.updated_at.isoformat(),
        }
        try:
            with self._engine.begin() as conn:
                updated = conn.execute(
                    requests_table.update()
                    .where(requests_table.c.id == request.id)
                    .values(**values)
                )
                if updated.rowcount == 0:
                    conn.execute(requests_table.insert().values(id=request.id, **values))
        except IntegrityError:
            # A concurrent writer inserted the same new id between our UPDATE
            # and INSERT — settle the race by updating.
            with self._engine.begin() as conn:
                conn.execute(
                    requests_table.update()
                    .where(requests_table.c.id == request.id)
                    .values(**values)
                )

    def get(self, request_id: str) -> ArchitectureRequest | None:
        """Load one request by id, or ``None`` when absent."""
        stmt = select(requests_table.c.data).where(requests_table.c.id == request_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return ArchitectureRequest.model_validate_json(row.data) if row else None

    def list(self) -> list[ArchitectureRequest]:
        """All requests, most recently updated first."""
        stmt = select(requests_table.c.data).order_by(requests_table.c.updated_at.desc())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [ArchitectureRequest.model_validate_json(row.data) for row in rows]

    def delete(self, request_id: str) -> None:
        """Remove the request with the given id (no-op when absent)."""
        with self._engine.begin() as conn:
            conn.execute(delete(requests_table).where(requests_table.c.id == request_id))


class ViewProjectRepository:
    """CRUD store for Studio :class:`~archflow.studio.models.ViewProject`s."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        init_db(engine)

    def save(self, project: ViewProject) -> None:
        """Insert or update the project (upsert keyed on ``project.id``)."""
        values = {
            "name": project.name,
            "data": project.model_dump_json(),
            "updated_at": project.updated_at.isoformat(),
        }
        with self._engine.begin() as conn:
            updated = conn.execute(
                view_projects_table.update()
                .where(view_projects_table.c.id == project.id)
                .values(**values)
            )
            if updated.rowcount == 0:
                conn.execute(view_projects_table.insert().values(id=project.id, **values))

    def get(self, project_id: str) -> ViewProject | None:
        """Load one project by id, or ``None`` when absent."""
        from archflow.studio.models import ViewProject

        stmt = select(view_projects_table.c.data).where(view_projects_table.c.id == project_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return ViewProject.model_validate_json(row.data) if row else None

    def list(self) -> list[ViewProject]:
        """All projects, most recently updated first."""
        from archflow.studio.models import ViewProject

        stmt = select(view_projects_table.c.data).order_by(
            view_projects_table.c.updated_at.desc()
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [ViewProject.model_validate_json(row.data) for row in rows]

    def delete(self, project_id: str) -> None:
        """Remove the project with the given id (no-op when absent)."""
        with self._engine.begin() as conn:
            conn.execute(
                delete(view_projects_table).where(view_projects_table.c.id == project_id)
            )


class EventLog:
    """Append-only audit trail of workflow events."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        init_db(engine)

    def append(self, event: Event) -> None:
        """Persist one event."""
        with self._engine.begin() as conn:
            conn.execute(
                events_table.insert().values(
                    id=event.id,
                    request_id=event.request_id,
                    type=event.type.value,
                    data=event.model_dump_json(),
                    occurred_at=event.occurred_at.isoformat(),
                )
            )

    def for_request(self, request_id: str) -> list[Event]:
        """Events for one request, oldest first."""
        stmt = (
            select(events_table.c.data)
            .where(events_table.c.request_id == request_id)
            # Chronological, with insertion order as the stable tiebreaker for
            # events created within the same timestamp resolution.
            .order_by(events_table.c.occurred_at.asc(), events_table.c.seq.asc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [Event.model_validate_json(row.data) for row in rows]
