"""Database access layer — a SQLAlchemy Engine + Session over Supabase Postgres.

This replaces the previous file-based sqlite3 layer. The schema itself lives in
``models.py`` and is managed by Alembic (``alembic upgrade head``); this module
only owns connection setup and session lifecycle.

Usage::

    from src.db import get_session

    with get_session() as session:
        session.add(Run(id=run_id, ...))
        # commit happens on clean exit; rollback on exception
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src import config as _cfg


def _build_url() -> URL | str:
    """Assemble the SQLAlchemy connection URL.

    A full ``DATABASE_URL`` (used by tests/CI) wins. Otherwise the URL is built
    from the discrete ``SUPABASE_DB_*`` parts with ``URL.create`` so that a
    password containing URL-reserved characters is escaped correctly.
    """
    if _cfg.DATABASE_URL:
        return _cfg.DATABASE_URL
    return URL.create(
        "postgresql+psycopg",
        username=_cfg.SUPABASE_DB_USER,
        password=_cfg.DB_PASSWORD,
        host=_cfg.SUPABASE_DB_HOST,
        port=_cfg.SUPABASE_DB_PORT,
        database=_cfg.SUPABASE_DB_NAME,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide pooled Engine (created lazily on first use)."""
    return create_engine(_build_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a Session, committing on clean exit and rolling back on error."""
    session = _get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
