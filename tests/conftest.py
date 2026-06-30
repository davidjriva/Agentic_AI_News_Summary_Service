"""Shared pytest fixtures.

DB-touching tests use a real, ephemeral Postgres via testcontainers (the same
engine path as production, since the models rely on Postgres-specific upserts).
Tests that don't assert persistence should not request the ``db`` fixture — they
keep mocking the external boundaries (LLM, SMTP, feeds) and never start a
container.
"""

import pytest
from testcontainers.postgres import PostgresContainer

from src import config as _cfg
from src import db as _db
from src.models import Base


@pytest.fixture(scope="session")
def _pg_engine():
    """Start one Postgres container for the whole test session and create schema."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        _cfg.DATABASE_URL = postgres.get_connection_url()
        # Drop cached engine/sessionmaker so they pick up the container URL.
        _db.get_engine.cache_clear()
        _db._get_sessionmaker.cache_clear()

        engine = _db.get_engine()
        Base.metadata.create_all(engine)
        try:
            yield engine
        finally:
            engine.dispose()
            _cfg.DATABASE_URL = None
            _db.get_engine.cache_clear()
            _db._get_sessionmaker.cache_clear()


@pytest.fixture()
def db(_pg_engine):
    """Yield a clean database (all tables truncated) for a DB-touching test."""
    with _pg_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    return _pg_engine
