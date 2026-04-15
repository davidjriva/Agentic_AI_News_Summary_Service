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
    conn.commit()
    return conn
