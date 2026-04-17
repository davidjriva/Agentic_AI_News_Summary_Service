"""Tests for src/main.py run_pipeline orchestration."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Test: tqdm stage bar
# ---------------------------------------------------------------------------

def test_run_pipeline_uses_tqdm(monkeypatch):
    """Stage bar should be created with total=6 during a dry run (6 stages including filter)."""
    bar_mock = MagicMock()
    bar_mock.__enter__ = MagicMock(return_value=bar_mock)
    bar_mock.__exit__ = MagicMock(return_value=False)
    tqdm_cls = MagicMock(return_value=bar_mock)

    with (
        patch("src.main.fetch_articles", return_value=[]),
        patch("src.main.process_articles", return_value=[]),
        patch("src.main.filter_articles", return_value=([], [])),
        patch("src.main.rank_articles", return_value=[]),
        patch("src.main.summarize_articles", return_value=[]),
        patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
        patch("src.main.get_connection"),
        patch("src.main.tqdm", tqdm_cls),
    ):
        from src.main import run_pipeline
        run_pipeline(dry_run=True)

    tqdm_cls.assert_called_once_with(total=7, desc="Pipeline", leave=True)
    assert bar_mock.update.call_count == 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(url: str = "http://example.com/1", relevance_score: int = 8) -> dict:
    return {
        "url": url,
        "title": "Test Article",
        "publication": "example.com",
        "relevance_score": relevance_score,
        "relevance_reason": "relevant to AI",
        "impact_score": 7,
        "authenticity_score": 7,
        "summary": "A test summary",
        "impact_reason": "",
        "authenticity_reason": "",
        "published_at": "2026-04-16T07:00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Patch src.main.get_connection (and src.db._DATA_DIR) to use isolated temp DB."""
    db_path = tmp_path / "test_state.db"

    def _make_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT,
                article_count INTEGER,
                html TEXT,
                error TEXT,
                dropped_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS run_articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                title        TEXT,
                url          TEXT,
                publication  TEXT,
                published_at TEXT,
                rank_score   REAL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS seen_articles (
                url TEXT PRIMARY KEY,
                seen_at TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS failed_articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                publication TEXT,
                reason      TEXT,
                failed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS filtered_articles (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           TEXT NOT NULL,
                url              TEXT,
                title            TEXT,
                publication      TEXT,
                relevance_score  INTEGER,
                relevance_reason TEXT
            )"""
        )
        conn.commit()
        return conn

    monkeypatch.setattr("src.main.get_connection", _make_conn)
    # Also patch src.db._DATA_DIR in case anything calls get_connection via src.db
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)

    # Initialize schema so tests can insert directly via sqlite3.connect
    init = _make_conn()
    init.close()

    return db_path


# ---------------------------------------------------------------------------
# Test: filter_articles called between process and rank
# ---------------------------------------------------------------------------

class TestFilterCalledBetweenProcessAndRank:
    """filter_articles must be called after process_articles and before rank_articles."""

    def test_filter_called_after_process_before_rank(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [_make_article("http://dropped.com", relevance_score=3)]
        call_order = []

        def mock_fetch():
            return [_make_article("http://kept.com"), _make_article("http://dropped.com")]

        def mock_process(articles, run_id=None):
            call_order.append("process")
            return articles

        def mock_filter(articles):
            call_order.append("filter")
            return kept, dropped

        def mock_rank(articles):
            call_order.append("rank")
            return articles

        with (
            patch("src.main.fetch_articles", side_effect=mock_fetch),
            patch("src.main.process_articles", side_effect=mock_process),
            patch("src.main.filter_articles", side_effect=mock_filter),
            patch("src.main.rank_articles", side_effect=mock_rank),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id="test-order-run")

        assert call_order == ["process", "filter", "rank"], (
            f"Expected process→filter→rank, got {call_order}"
        )

    def test_rank_receives_only_kept_articles(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [_make_article("http://dropped.com", relevance_score=3)]
        rank_input = {}

        def mock_rank(articles):
            rank_input["articles"] = articles
            return articles

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.process_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", side_effect=mock_rank),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id="test-rank-input-run")

        assert rank_input["articles"] == kept
        assert len(rank_input["articles"]) == 1
        assert rank_input["articles"][0]["url"] == "http://kept.com"


# ---------------------------------------------------------------------------
# Test: dropped articles persisted to filtered_articles table
# ---------------------------------------------------------------------------

class TestDroppedArticlesPersisted:
    """Dropped articles must be written to filtered_articles table."""

    def test_dropped_articles_inserted_to_db(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [
            _make_article("http://dropped1.com", relevance_score=3),
            _make_article("http://dropped2.com", relevance_score=2),
        ]
        dropped[0]["title"] = "Dropped Article 1"
        dropped[1]["title"] = "Dropped Article 2"

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.process_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-persist-run")

        conn = sqlite3.connect(str(temp_db))
        rows = conn.execute(
            "SELECT url, title, relevance_score FROM filtered_articles WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        urls = {row[0] for row in rows}
        assert "http://dropped1.com" in urls
        assert "http://dropped2.com" in urls

    def test_no_db_write_when_no_dropped_articles(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=8)]

        with (
            patch("src.main.fetch_articles", return_value=kept),
            patch("src.main.process_articles", return_value=kept),
            patch("src.main.filter_articles", return_value=(kept, [])),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-no-dropped-run")

        conn = sqlite3.connect(str(temp_db))
        rows = conn.execute(
            "SELECT * FROM filtered_articles WHERE run_id = ?", (run_id,)
        ).fetchall()
        conn.close()

        assert len(rows) == 0

    def test_dropped_articles_store_relevance_reason(self, temp_db):
        kept = []
        dropped = [_make_article("http://dropped.com", relevance_score=4)]
        dropped[0]["relevance_reason"] = "not about AI"

        with (
            patch("src.main.fetch_articles", return_value=dropped),
            patch("src.main.process_articles", return_value=dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-reason-run")

        conn = sqlite3.connect(str(temp_db))
        row = conn.execute(
            "SELECT relevance_reason FROM filtered_articles WHERE run_id = ?", (run_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "not about AI"


# ---------------------------------------------------------------------------
# Test: runs table updated with dropped_count and failed_count
# ---------------------------------------------------------------------------

class TestRunCountersUpdated:
    """The final UPDATE runs SET ... must include dropped_count and failed_count."""

    def test_dropped_count_stored_in_runs(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=9)]
        dropped = [
            _make_article("http://d1.com", relevance_score=2),
            _make_article("http://d2.com", relevance_score=3),
        ]

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.process_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-counters-run")

        conn = sqlite3.connect(str(temp_db))
        row = conn.execute(
            "SELECT dropped_count, failed_count FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 2  # dropped_count
        assert row[1] == 0  # failed_count (no failed_articles rows)

    def test_failed_count_reads_from_failed_articles_table(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=9)]

        # Pre-insert failed_articles rows for this run_id
        run_id = "test-failed-count-run"
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO failed_articles (run_id, url, title, publication, reason) VALUES (?,?,?,?,?)",
            (run_id, "http://fail.com", "Fail Article", "fail.com", "LLM timeout"),
        )
        conn.commit()
        conn.close()

        with (
            patch("src.main.fetch_articles", return_value=kept),
            patch("src.main.process_articles", return_value=kept),
            patch("src.main.filter_articles", return_value=(kept, [])),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id=run_id)

        conn = sqlite3.connect(str(temp_db))
        row = conn.execute(
            "SELECT dropped_count, failed_count FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 0  # dropped_count
        assert row[1] == 1  # failed_count from failed_articles

    def test_both_dropped_and_failed_counted(self, temp_db):
        kept = [_make_article("http://kept.com", relevance_score=9)]
        dropped = [_make_article("http://dropped.com", relevance_score=1)]
        run_id = "test-both-run"

        # Pre-insert 2 failed_articles rows
        conn = sqlite3.connect(str(temp_db))
        for i in range(2):
            conn.execute(
                "INSERT INTO failed_articles (run_id, url, title, publication, reason) VALUES (?,?,?,?,?)",
                (run_id, f"http://fail{i}.com", f"Fail {i}", "fail.com", "error"),
            )
        conn.commit()
        conn.close()

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.process_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id=run_id)

        conn = sqlite3.connect(str(temp_db))
        row = conn.execute(
            "SELECT dropped_count, failed_count FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()

        assert row[0] == 1  # dropped_count
        assert row[1] == 2  # failed_count
