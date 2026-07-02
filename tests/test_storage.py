"""Tests for the SQLAlchemy-backed request repository and event log."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from archflow.domain.events import Event, EventType
from archflow.domain.models import ArchitectureRequest, Stage, Stakeholder
from archflow.storage import EventLog, RequestRepository, create_db_engine


@pytest.fixture()
def db_engine(tmp_path: Path) -> Engine:
    return create_db_engine(f"sqlite:///{tmp_path}/t.db")


def test_request_round_trip(db_engine: Engine) -> None:
    repo = RequestRepository(db_engine)
    request = ArchitectureRequest(
        title="Round trip",
        description="desc",
        requester="alice",
        impacted_domains=["finance"],
        stakeholders=[Stakeholder(name="Eve", concerns=["cost"])],
    )
    repo.save(request)

    loaded = repo.get(request.id)
    assert loaded is not None
    assert loaded.model_dump() == request.model_dump()
    assert loaded.stakeholders[0].name == "Eve"


def test_get_missing_returns_none(db_engine: Engine) -> None:
    repo = RequestRepository(db_engine)
    assert repo.get("nope") is None


def test_save_upserts_without_duplicating(db_engine: Engine) -> None:
    repo = RequestRepository(db_engine)
    request = ArchitectureRequest(title="v1")
    repo.save(request)
    request.title = "v2"
    request.stage = Stage.TRIAGE
    request.touch()
    repo.save(request)

    all_requests = repo.list()
    assert len(all_requests) == 1
    assert all_requests[0].title == "v2"
    assert all_requests[0].stage == Stage.TRIAGE


def test_list_orders_by_updated_at_desc(db_engine: Engine) -> None:
    repo = RequestRepository(db_engine)
    older = ArchitectureRequest(title="older")
    older.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer = ArchitectureRequest(title="newer")
    newer.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    repo.save(older)
    repo.save(newer)

    titles = [r.title for r in repo.list()]
    assert titles == ["newer", "older"]


def test_delete(db_engine: Engine) -> None:
    repo = RequestRepository(db_engine)
    request = ArchitectureRequest(title="bye")
    repo.save(request)
    repo.delete(request.id)
    assert repo.get(request.id) is None
    assert repo.list() == []


def test_event_log_append_and_ordering(db_engine: Engine) -> None:
    log = EventLog(db_engine)
    second = Event(
        request_id="r1",
        type=EventType.STAGE_ENTERED,
        occurred_at=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
    )
    first = Event(
        request_id="r1",
        type=EventType.REQUEST_CREATED,
        payload={"title": "t"},
        occurred_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )
    other = Event(request_id="r2", type=EventType.REQUEST_CREATED)
    log.append(second)  # inserted out of chronological order on purpose
    log.append(first)
    log.append(other)

    events = log.for_request("r1")
    assert [e.type for e in events] == [EventType.REQUEST_CREATED, EventType.STAGE_ENTERED]
    assert events[0].payload == {"title": "t"}
    assert all(e.request_id == "r1" for e in events)
