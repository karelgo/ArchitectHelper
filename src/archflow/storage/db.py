"""Database engine and table definitions for ArchFlow persistence.

Documents are stored as JSON blobs (pydantic ``model_dump_json``) next to a
few queryable columns; this keeps the schema stable while the domain evolves.
"""

from __future__ import annotations

from sqlalchemy import Column, Engine, MetaData, Table, Text, create_engine

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
    Column("id", Text, primary_key=True),
    Column("request_id", Text, nullable=False, index=True),
    Column("type", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("occurred_at", Text, nullable=False),
)


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy 2.0 engine for the given database URL."""
    return create_engine(database_url)


def init_db(engine: Engine) -> None:
    """Create all ArchFlow tables (idempotent)."""
    metadata.create_all(engine)
