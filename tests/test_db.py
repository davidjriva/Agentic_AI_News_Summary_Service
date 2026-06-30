import pytest
from sqlalchemy import inspect

from src.db import get_session
from src.models import Run

_EXPECTED_TABLES = {
    "seen_articles",
    "runs",
    "run_articles",
    "failed_articles",
    "filtered_articles",
    "article_scores",
}


def test_expected_tables_exist(db):
    tables = set(inspect(db).get_table_names())
    assert _EXPECTED_TABLES <= tables


def test_runs_has_dropped_and_failed_count(db):
    cols = {c["name"] for c in inspect(db).get_columns("runs")}
    assert "dropped_count" in cols
    assert "failed_count" in cols


def test_article_scores_has_expected_columns(db):
    cols = {c["name"] for c in inspect(db).get_columns("article_scores")}
    expected = {
        "url", "impact_score", "authenticity_score", "relevance_score",
        "impact_reason", "authenticity_reason", "relevance_reason", "cached_at",
    }
    assert expected == cols


def test_get_session_commits_on_clean_exit(db):
    with get_session() as session:
        session.add(Run(id="r1", status="running"))

    with get_session() as session:
        assert session.get(Run, "r1").status == "running"


def test_get_session_rolls_back_on_error(db):
    with pytest.raises(ValueError):
        with get_session() as session:
            session.add(Run(id="r2", status="running"))
            raise ValueError("boom")

    with get_session() as session:
        assert session.get(Run, "r2") is None
