import sqlite3
import pytest
from src.db import get_connection


def test_failed_articles_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_articles'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_filtered_articles_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='filtered_articles'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_runs_has_dropped_count_column(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(runs)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "dropped_count" in cols
    assert "failed_count" in cols
    conn.close()
