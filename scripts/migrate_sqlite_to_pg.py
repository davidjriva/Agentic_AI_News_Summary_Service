"""One-time backfill: copy all data from the legacy SQLite DB into Supabase Postgres.

Run this *after* ``alembic upgrade head`` has created the schema:

    poetry run python -m scripts.migrate_sqlite_to_pg

Idempotent: every insert uses ``ON CONFLICT DO NOTHING`` on the primary key, so
re-running after a partial or complete load adds only missing rows. Surrogate
``id`` values are preserved from SQLite, and the Postgres identity sequences are
advanced past the copied maximum so future app inserts don't collide.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import get_engine, get_session
from src.models import (
    ArticleScore,
    FailedArticle,
    FilteredArticle,
    Run,
    RunArticle,
    SeenArticle,
)

_SQLITE_PATH = Path(__file__).parent.parent / "data" / "state.db"

# Columns on each table that map to Postgres ``timestamptz`` and are stored in
# SQLite as naive "YYYY-MM-DD HH:MM:SS" strings — parsed to aware datetimes.
_TS_COLUMNS = {
    "failed_articles": ("failed_at",),
    "article_scores": ("cached_at",),
}

# (sqlite table, ORM model, primary-key column, has integer identity sequence)
_TABLES = [
    ("seen_articles", SeenArticle, "url", False),
    ("runs", Run, "id", False),  # id is a text UUID, not a sequence
    ("run_articles", RunArticle, "id", True),
    ("failed_articles", FailedArticle, "id", True),
    ("filtered_articles", FilteredArticle, "id", True),
    ("article_scores", ArticleScore, "url", False),
]


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a SQLite timestamp string into a UTC-aware datetime."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_sqlite_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    for col in _TS_COLUMNS.get(table, ()):
        for row in rows:
            row[col] = _parse_ts(row.get(col))
    return rows


def _reset_identity(table: str, has_identity: bool) -> None:
    """Advance an identity sequence past the max copied id."""
    if not has_identity:
        return
    with get_session() as session:
        session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"(SELECT MAX(id) FROM {table}))"
            )
        )


def main() -> None:
    if not _SQLITE_PATH.exists():
        raise SystemExit(f"SQLite database not found at {_SQLITE_PATH}")

    sqlite_conn = sqlite3.connect(f"file:{_SQLITE_PATH}?mode=ro", uri=True)
    print(f"Reading from {_SQLITE_PATH}\n")

    try:
        for table, model, pk, has_identity in _TABLES:
            rows = _read_sqlite_rows(sqlite_conn, table)
            if rows:
                stmt = pg_insert(model).on_conflict_do_nothing(index_elements=[pk])
                with get_session() as session:
                    session.execute(stmt, rows)
                _reset_identity(table, has_identity)

            # Verify the Postgres count matches what SQLite holds.
            with get_session() as session:
                pg_count = session.scalar(select(func.count()).select_from(model))
            status = "ok" if pg_count >= len(rows) else "MISMATCH"
            print(f"  {table:<18} sqlite={len(rows):<5} postgres={pg_count:<5} [{status}]")
    finally:
        sqlite_conn.close()

    get_engine().dispose()
    print("\nBackfill complete.")


if __name__ == "__main__":
    main()
