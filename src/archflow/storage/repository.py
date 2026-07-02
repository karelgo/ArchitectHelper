"""Repositories persisting requests and events as JSON documents."""

from __future__ import annotations

from sqlalchemy import Engine, delete, select

from archflow.domain.events import Event
from archflow.domain.models import ArchitectureRequest
from archflow.storage.db import events_table, init_db, requests_table


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
        with self._engine.begin() as conn:
            updated = conn.execute(
                requests_table.update()
                .where(requests_table.c.id == request.id)
                .values(**values)
            )
            if updated.rowcount == 0:
                conn.execute(requests_table.insert().values(id=request.id, **values))

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
            .order_by(events_table.c.occurred_at.asc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [Event.model_validate_json(row.data) for row in rows]
