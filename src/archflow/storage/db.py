"""Database engine and table definitions for ArchFlow persistence.

Documents are stored as JSON blobs (pydantic ``model_dump_json``) next to a
few queryable columns; this keeps the schema stable while the domain evolves.
"""

from __future__ import annotations

from sqlalchemy import Column, Engine, Integer, MetaData, Table, Text, create_engine

metadata = MetaData()

requests_table = Table(
    "requests",
    metadata,
    Column("id", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

events_table = Table(
    "events",
    metadata,
    # Autoincrement sequence: the audit trail's stable ordering tiebreaker
    # for events created within the same timestamp resolution.
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", Text, nullable=False, unique=True),
    Column("request_id", Text, nullable=False, index=True),
    Column("type", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("occurred_at", Text, nullable=False),
)

view_projects_table = Table(
    "view_projects",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy 2.0 engine for the given database URL.

    SQLite connections allow cross-thread use: FastAPI serves sync endpoints
    from a threadpool, so pooled connections migrate between threads (SQLite
    itself serializes writes).
    """
    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    return create_engine(database_url, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    """Create all ArchFlow tables (idempotent)."""
    metadata.create_all(engine)
