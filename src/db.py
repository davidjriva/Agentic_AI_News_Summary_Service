import sqlite3
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data"

_CREATE_SEEN_ARTICLES = """
CREATE TABLE IF NOT EXISTS seen_articles (
    url TEXT PRIMARY KEY,
    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT,
    article_count INTEGER,
    html TEXT,
    error TEXT
);
"""

_CREATE_RUN_ARTICLES = """
CREATE TABLE IF NOT EXISTS run_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    publication  TEXT,
    published_at TEXT
);
"""

_CREATE_FAILED_ARTICLES = """
CREATE TABLE IF NOT EXISTS failed_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    url         TEXT,
    title       TEXT,
    publication TEXT,
    reason      TEXT,
    failed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_FILTERED_ARTICLES = """
CREATE TABLE IF NOT EXISTS filtered_articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    url              TEXT,
    title            TEXT,
    publication      TEXT,
    relevance_score  INTEGER,
    relevance_reason TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a fresh sqlite3 connection with row_factory set to sqlite3.Row.

    Creates the data/ directory and required tables on first use.
    Callers are expected to use the connection as a context manager.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _DATA_DIR / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_SEEN_ARTICLES)
    conn.execute(_CREATE_RUNS)
    conn.execute(_CREATE_RUN_ARTICLES)
    conn.execute(_CREATE_FAILED_ARTICLES)
    conn.execute(_CREATE_FILTERED_ARTICLES)
    # Migrate existing runs table — safe to run repeatedly
    for col_sql in (
        "ALTER TABLE runs ADD COLUMN dropped_count INTEGER DEFAULT 0",
        "ALTER TABLE runs ADD COLUMN failed_count INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn
