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


def test_article_scores_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='article_scores'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_article_scores_has_expected_columns(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(article_scores)")
    cols = {row[1] for row in cursor.fetchall()}
    expected = {
        "url", "impact_score", "authenticity_score", "relevance_score",
        "impact_reason", "authenticity_reason", "relevance_reason", "cached_at",
    }
    assert expected == cols
    conn.close()
