"""Tests for src/main.py run_pipeline orchestration."""

from unittest.mock import MagicMock, patch

from sqlalchemy import select

from src.db import get_session
from src.models import FailedArticle, FilteredArticle, Run, SeenArticle


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


def _seed(*objs):
    with get_session() as session:
        session.add_all(objs)


def _get_run(run_id):
    with get_session() as session:
        return session.get(Run, run_id)


def _filtered_for(run_id):
    with get_session() as session:
        return session.execute(
            select(FilteredArticle).where(FilteredArticle.run_id == run_id)
        ).scalars().all()


def _seen_urls():
    with get_session() as session:
        return set(session.scalars(select(SeenArticle.url)).all())


# ---------------------------------------------------------------------------
# Test: tqdm stage bar
# ---------------------------------------------------------------------------

def test_run_pipeline_uses_tqdm(db):
    """Stage bar should be created with total=8 during a dry run."""
    bar_mock = MagicMock()
    bar_mock.__enter__ = MagicMock(return_value=bar_mock)
    bar_mock.__exit__ = MagicMock(return_value=False)
    tqdm_cls = MagicMock(return_value=bar_mock)

    with (
        patch("src.main.fetch_articles", return_value=[]),
        patch("src.main.triage_articles", return_value=[]),
        patch("src.main.score_articles", return_value=[]),
        patch("src.main.filter_articles", return_value=([], [])),
        patch("src.main.rank_articles", return_value=[]),
        patch("src.main.summarize_articles", return_value=[]),
        patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
        patch("src.main.tqdm", tqdm_cls),
    ):
        from src.main import run_pipeline
        run_pipeline(dry_run=True)

    tqdm_cls.assert_called_once_with(total=8, desc="Pipeline", leave=True)
    assert bar_mock.update.call_count == 8


# ---------------------------------------------------------------------------
# Test: filter_articles called between process and rank
# ---------------------------------------------------------------------------

def test_send_newsletter_receives_run_id(db):
    """run_pipeline must pass its run_id to send_newsletter (for delivery tracking)."""
    kept = [_make_article("http://kept.com", relevance_score=9)]
    captured = {}

    def capture_send(html, plain, run_time, run_id=None):
        captured["run_id"] = run_id

    with (
        patch("src.main.fetch_articles", return_value=kept),
        patch("src.main.triage_articles", return_value=kept),
        patch("src.main.score_articles", return_value=kept),
        patch("src.main.filter_articles", return_value=(kept, [])),
        patch("src.main.rank_articles", return_value=kept),
        patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
        patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
        patch("src.main.send_newsletter", side_effect=capture_send),
    ):
        from src.main import run_pipeline
        run_pipeline(run_id="rid-123")

    assert captured["run_id"] == "rid-123"


class TestFilterCalledBetweenProcessAndRank:
    """filter_articles must run after triage, and score_articles between filter and rank."""

    def test_filter_called_after_process_before_rank(self, db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [_make_article("http://dropped.com", relevance_score=3)]
        call_order = []

        def mock_fetch():
            return [_make_article("http://kept.com"), _make_article("http://dropped.com")]

        def mock_triage(articles, run_id=None):
            call_order.append("triage")
            return articles

        def mock_filter(articles):
            call_order.append("filter")
            return kept, dropped

        def mock_score(articles, run_id=None):
            call_order.append("score")
            return articles

        def mock_rank(articles):
            call_order.append("rank")
            return articles

        with (
            patch("src.main.fetch_articles", side_effect=mock_fetch),
            patch("src.main.triage_articles", side_effect=mock_triage),
            patch("src.main.filter_articles", side_effect=mock_filter),
            patch("src.main.score_articles", side_effect=mock_score),
            patch("src.main.rank_articles", side_effect=mock_rank),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id="test-order-run")

        assert call_order == ["triage", "filter", "score", "rank"], (
            f"Expected triage→filter→score→rank, got {call_order}"
        )

    def test_rank_receives_only_kept_articles(self, db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [_make_article("http://dropped.com", relevance_score=3)]
        rank_input = {}

        def mock_rank(articles):
            rank_input["articles"] = articles
            return articles

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.triage_articles", return_value=kept + dropped),
            patch("src.main.score_articles", side_effect=lambda articles, **kw: articles),
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

    def test_dropped_articles_inserted_to_db(self, db):
        kept = [_make_article("http://kept.com", relevance_score=8)]
        dropped = [
            _make_article("http://dropped1.com", relevance_score=3),
            _make_article("http://dropped2.com", relevance_score=2),
        ]
        dropped[0]["title"] = "Dropped Article 1"
        dropped[1]["title"] = "Dropped Article 2"

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.triage_articles", return_value=kept + dropped),
            patch("src.main.score_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-persist-run")

        rows = _filtered_for(run_id)
        assert len(rows) == 2
        urls = {row.url for row in rows}
        assert "http://dropped1.com" in urls
        assert "http://dropped2.com" in urls

    def test_no_db_write_when_no_dropped_articles(self, db):
        kept = [_make_article("http://kept.com", relevance_score=8)]

        with (
            patch("src.main.fetch_articles", return_value=kept),
            patch("src.main.triage_articles", return_value=kept),
            patch("src.main.score_articles", return_value=kept),
            patch("src.main.filter_articles", return_value=(kept, [])),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-no-dropped-run")

        assert len(_filtered_for(run_id)) == 0

    def test_dropped_articles_store_relevance_reason(self, db):
        kept = []
        dropped = [_make_article("http://dropped.com", relevance_score=4)]
        dropped[0]["relevance_reason"] = "not about AI"

        with (
            patch("src.main.fetch_articles", return_value=dropped),
            patch("src.main.triage_articles", return_value=dropped),
            patch("src.main.score_articles", return_value=dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-reason-run")

        rows = _filtered_for(run_id)
        assert len(rows) == 1
        assert rows[0].relevance_reason == "not about AI"


# ---------------------------------------------------------------------------
# Test: runs table updated with dropped_count and failed_count
# ---------------------------------------------------------------------------

class TestRunCountersUpdated:
    """The final UPDATE runs SET ... must include dropped_count and failed_count."""

    def test_dropped_count_stored_in_runs(self, db):
        kept = [_make_article("http://kept.com", relevance_score=9)]
        dropped = [
            _make_article("http://d1.com", relevance_score=2),
            _make_article("http://d2.com", relevance_score=3),
        ]

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.triage_articles", return_value=kept + dropped),
            patch("src.main.score_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_id = run_pipeline(run_id="test-counters-run")

        run = _get_run(run_id)
        assert run is not None
        assert run.dropped_count == 2
        assert run.failed_count == 0

    def test_failed_count_reads_from_failed_articles_table(self, db):
        kept = [_make_article("http://kept.com", relevance_score=9)]
        run_id = "test-failed-count-run"

        # Pre-insert a failed_articles row for this run_id.
        _seed(FailedArticle(run_id=run_id, url="http://fail.com", title="Fail Article",
                            publication="fail.com", reason="LLM timeout"))

        with (
            patch("src.main.fetch_articles", return_value=kept),
            patch("src.main.triage_articles", return_value=kept),
            patch("src.main.score_articles", return_value=kept),
            patch("src.main.filter_articles", return_value=(kept, [])),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id=run_id)

        run = _get_run(run_id)
        assert run is not None
        assert run.dropped_count == 0
        assert run.failed_count == 1

    def test_both_dropped_and_failed_counted(self, db):
        kept = [_make_article("http://kept.com", relevance_score=9)]
        dropped = [_make_article("http://dropped.com", relevance_score=1)]
        run_id = "test-both-run"

        # Pre-insert 2 failed_articles rows.
        _seed(*[
            FailedArticle(run_id=run_id, url=f"http://fail{i}.com", title=f"Fail {i}",
                         publication="fail.com", reason="error")
            for i in range(2)
        ])

        with (
            patch("src.main.fetch_articles", return_value=kept + dropped),
            patch("src.main.triage_articles", return_value=kept + dropped),
            patch("src.main.score_articles", return_value=kept + dropped),
            patch("src.main.filter_articles", return_value=(kept, dropped)),
            patch("src.main.rank_articles", return_value=kept),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id=run_id)

        run = _get_run(run_id)
        assert run.dropped_count == 1
        assert run.failed_count == 2


# ---------------------------------------------------------------------------
# Test: top-N URLs written to seen_articles after ranking
# ---------------------------------------------------------------------------

def test_top_n_urls_written_to_seen_articles(db):
    """After ranking, exactly the top-N article URLs must be in seen_articles."""
    from src.config import TOP_N
    from src.main import run_pipeline

    total = TOP_N + 5
    all_urls = [f"https://example.com/article-{i}" for i in range(total)]

    def _article(i, url):
        return {
            "url": url,
            "title": f"Title {i}",
            "publication": "example.com",
            "published_at": "2026-04-18T10:00:00+00:00",
            "rank_score": float(total - i),
            "impact_score": 9,
            "authenticity_score": 8,
            "relevance_score": 9,
            "summary": "A summary.",
        }

    ranked_articles = [_article(i, url) for i, url in enumerate(all_urls)]
    top_articles = ranked_articles[:TOP_N]

    with patch("src.main.fetch_articles", return_value=[]), \
         patch("src.main.triage_articles", return_value=ranked_articles), \
         patch("src.main.score_articles", return_value=ranked_articles), \
         patch("src.main.filter_articles", return_value=(ranked_articles, [])), \
         patch("src.main.rank_articles", return_value=ranked_articles), \
         patch("src.main.summarize_articles", return_value=top_articles), \
         patch("src.main.render_newsletter", return_value=("<html/>", "plain")), \
         patch("src.main.send_newsletter"):
        run_pipeline(dry_run=True, run_id="test-run-id")

    seen = _seen_urls()
    expected = {a["url"] for a in ranked_articles[:TOP_N]}
    unexpected = {a["url"] for a in ranked_articles[TOP_N:]}

    assert seen == expected, f"seen_articles must contain exactly top-{TOP_N} URLs"
    assert not seen & unexpected, "Articles beyond TOP_N must not be in seen_articles"
