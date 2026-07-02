"""Tests for src/server.py FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.db import get_session
from src.models import FailedArticle, FilteredArticle, Run, RunArticle, Subscriber


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_running_state():
    """Ensure _is_running is False before each test."""
    import src.server as srv
    srv._is_running = False
    yield
    srv._is_running = False


@pytest.fixture()
def client(db):
    """TestClient backed by a clean testcontainers Postgres (via the db fixture)."""
    from src.server import app
    return TestClient(app)


def _seed(*objs):
    """Persist ORM objects through the application's session."""
    with get_session() as session:
        session.add_all(objs)


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

def test_status_not_running(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json() == {"running": False}


def test_status_while_running(client):
    import src.server as srv
    srv._is_running = True
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json() == {"running": True}


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------

def test_post_run_returns_run_id(client):
    with patch("src.server.run_pipeline", return_value=None):
        response = client.post("/run")
    assert response.status_code == 200
    data = response.json()
    assert "run_id" in data
    assert isinstance(data["run_id"], str)
    assert len(data["run_id"]) > 0


def test_post_run_conflict_when_already_running(client):
    import src.server as srv
    srv._is_running = True
    response = client.post("/run")
    assert response.status_code == 409


def test_post_run_passes_run_id_to_pipeline(client):
    called_with = {}

    def capture_run(run_id, dry_run, clean):
        called_with["run_id"] = run_id
        called_with["dry_run"] = dry_run
        called_with["clean"] = clean

    with patch("src.server.run_pipeline", side_effect=capture_run):
        response = client.post("/run")

    # Wait briefly for background thread to finish
    import time
    time.sleep(0.05)

    assert response.status_code == 200
    assert called_with.get("run_id") == response.json()["run_id"]
    assert called_with.get("dry_run") is False
    assert called_with.get("clean") is False


def test_post_run_clean_passes_clean_true(client):
    called_with = {}

    def capture_run(run_id, dry_run, clean):
        called_with["clean"] = clean

    with patch("src.server.run_pipeline", side_effect=capture_run):
        response = client.post("/run?clean=true")

    import time
    time.sleep(0.05)

    assert response.status_code == 200
    assert called_with.get("clean") is True


# ---------------------------------------------------------------------------
# GET /runs
# ---------------------------------------------------------------------------

def test_list_runs_empty(client):
    response = client.get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_runs_returns_records(client):
    _seed(Run(id="run-1", started_at="2026-04-14T07:00:00", status="success", article_count=15))

    response = client.get("/runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "run-1"
    assert data[0]["status"] == "success"
    assert data[0]["article_count"] == 15


# ---------------------------------------------------------------------------
# GET /runs/{run_id} (detail wrapper)
# ---------------------------------------------------------------------------

def test_get_run_not_found(client):
    response = client.get("/runs/nonexistent-id")
    assert response.status_code == 404


def test_get_run_renders_detail_page(client):
    html_content = "<html><body>Newsletter content</body></html>"
    _seed(Run(id="run-2", started_at="2026-04-14T07:00:00", status="success", article_count=10, html=html_content))

    response = client.get("/runs/run-2")
    assert response.status_code == 200
    assert "Articles by Source" in response.text
    assert "/runs/run-2/newsletter" in response.text


def test_get_run_error_status_returns_404(client):
    _seed(Run(id="run-3", started_at="2026-04-14T07:00:00", status="error", error="Something went wrong"))

    response = client.get("/runs/run-3")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/newsletter (raw HTML)
# ---------------------------------------------------------------------------

def test_get_run_newsletter_returns_raw_html(client):
    html_content = "<html><body>Newsletter content</body></html>"
    _seed(Run(id="run-5", started_at="2026-04-14T07:00:00", status="success", article_count=10, html=html_content))

    response = client.get("/runs/run-5/newsletter")
    assert response.status_code == 200
    assert "Newsletter content" in response.text


def test_get_run_newsletter_not_found(client):
    response = client.get("/runs/nonexistent-id/newsletter")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET / (dashboard)
# ---------------------------------------------------------------------------

def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Agentic Times" in response.text


def test_dashboard_shows_run_history(client):
    _seed(Run(id="run-4", started_at="2026-04-14T07:00:00", status="success", article_count=12))

    response = client.get("/")
    assert response.status_code == 200
    assert "run-4" in response.text or "Success" in response.text


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------

class TestMetricsRoute:
    def test_metrics_renders(self, client):
        """GET /metrics returns 200 with expected stat values in body."""
        _seed(
            Run(id="m-run-1", started_at="2026-04-14T07:00:00", completed_at="2026-04-14T07:05:00",
                status="success", article_count=8),
            RunArticle(run_id="m-run-1", title="Test Article", url="https://example.com/1", publication="TechCrunch"),
        )

        response = client.get("/metrics")
        assert response.status_code == 200
        assert "Metrics" in response.text
        # Stat values present
        assert "1" in response.text          # total_runs = 1
        assert "TechCrunch" in response.text  # source label in chart data

    def test_metrics_empty_state(self, client):
        """GET /metrics with empty DB returns 200 (no crash on empty charts)."""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "Metrics" in response.text
        # Empty JSON arrays rendered safely
        assert "[]" in response.text

    def test_subscribers_stat_counts_confirmed_only(self, client):
        """The Subscribers stat reflects confirmed subscribers, not RECIPIENTS."""
        _seed(
            *[Subscriber(email=f"c{i}@example.com", status="confirmed", unsubscribe_token=f"t{i}") for i in range(7)],
            Subscriber(email="p@example.com", status="pending", unsubscribe_token="tp"),
            Subscriber(email="u@example.com", status="unsubscribed", unsubscribe_token="tu"),
        )
        response = client.get("/metrics")
        assert response.status_code == 200
        assert '<div class="stat-value">7</div>' in response.text


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/filtered
# ---------------------------------------------------------------------------

class TestFilteredArticlesEndpoint:
    def test_returns_empty_list_when_no_filtered_articles(self, client):
        response = client.get("/runs/nonexistent-run/filtered")
        assert response.status_code == 200
        assert response.json() == {"filtered": []}

    def test_returns_filtered_articles_for_run(self, client):
        _seed(
            Run(id="filt-run-1", started_at="2026-04-14T07:00:00", status="success", article_count=5),
            FilteredArticle(
                run_id="filt-run-1",
                url="https://example.com/dropped",
                title="Dropped Article",
                publication="TechCrunch",
                relevance_score=3,
                relevance_reason="Not relevant to AI",
            ),
        )

        response = client.get("/runs/filt-run-1/filtered")
        assert response.status_code == 200
        data = response.json()
        assert "filtered" in data
        assert len(data["filtered"]) == 1
        article = data["filtered"][0]
        assert article["title"] == "Dropped Article"
        assert article["publication"] == "TechCrunch"
        assert article["relevance_score"] == 3
        assert article["relevance_reason"] == "Not relevant to AI"

    def test_only_returns_articles_for_requested_run(self, client):
        for run_id in ("filt-run-a", "filt-run-b"):
            _seed(
                Run(id=run_id, started_at="2026-04-14T07:00:00", status="success"),
                FilteredArticle(
                    run_id=run_id,
                    url=f"https://example.com/{run_id}",
                    title=f"Article for {run_id}",
                    publication="Source",
                    relevance_score=2,
                    relevance_reason="Low relevance",
                ),
            )

        response = client.get("/runs/filt-run-a/filtered")
        assert response.status_code == 200
        data = response.json()
        assert len(data["filtered"]) == 1
        assert data["filtered"][0]["title"] == "Article for filt-run-a"


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/failed
# ---------------------------------------------------------------------------

class TestFailedArticlesEndpoint:
    def test_returns_empty_list_when_no_failed_articles(self, client):
        response = client.get("/runs/nonexistent-run/failed")
        assert response.status_code == 200
        assert response.json() == {"failed": []}

    def test_returns_failed_articles_for_run(self, client):
        _seed(
            Run(id="fail-run-1", started_at="2026-04-14T07:00:00", status="success", article_count=5),
            FailedArticle(
                run_id="fail-run-1",
                url="https://example.com/failed",
                title="Failed Article",
                publication="Wired",
                reason="LLM timeout",
            ),
        )

        response = client.get("/runs/fail-run-1/failed")
        assert response.status_code == 200
        data = response.json()
        assert "failed" in data
        assert len(data["failed"]) == 1
        article = data["failed"][0]
        assert article["title"] == "Failed Article"
        assert article["publication"] == "Wired"
        assert article["reason"] == "LLM timeout"

    def test_only_returns_articles_for_requested_run(self, client):
        for run_id in ("fail-run-a", "fail-run-b"):
            _seed(
                Run(id=run_id, started_at="2026-04-14T07:00:00", status="success"),
                FailedArticle(
                    run_id=run_id,
                    url=f"https://example.com/{run_id}",
                    title=f"Failed for {run_id}",
                    publication="Source",
                    reason="error",
                ),
            )

        response = client.get("/runs/fail-run-a/failed")
        assert response.status_code == 200
        data = response.json()
        assert len(data["failed"]) == 1
        assert data["failed"][0]["title"] == "Failed for fail-run-a"


# ---------------------------------------------------------------------------
# Dashboard dropped_count / failed_count columns
# ---------------------------------------------------------------------------

class TestDashboardDroppedFailedColumns:
    def test_dashboard_has_dropped_and_failed_headers(self, client):
        _seed(Run(id="hdr-run-1", started_at="2026-04-14T07:00:00", status="success",
                  article_count=8, dropped_count=2, failed_count=1))

        response = client.get("/")
        assert response.status_code == 200
        assert "Dropped" in response.text
        assert "Failed" in response.text

    def test_dashboard_shows_dropped_and_failed_counts(self, client):
        _seed(Run(id="dash-run-1", started_at="2026-04-14T07:00:00", status="success",
                  article_count=10, dropped_count=4, failed_count=2))

        response = client.get("/")
        assert response.status_code == 200
        assert "4" in response.text   # dropped_count
        assert "2" in response.text   # failed_count


# ---------------------------------------------------------------------------
# GET /preview
# ---------------------------------------------------------------------------

class TestPreviewRoute:
    @pytest.fixture()
    def preview_client(self):
        from src.server import app
        return TestClient(app)

    def test_preview_returns_200(self, preview_client):
        response = preview_client.get("/preview")
        assert response.status_code == 200

    def test_preview_contains_banner(self, preview_client):
        response = preview_client.get("/preview")
        assert "Preview Mode" in response.text

    def test_preview_contains_articles_by_source(self, preview_client):
        response = preview_client.get("/preview")
        assert "Articles by Source" in response.text

    def test_preview_shows_fake_article_titles(self, preview_client):
        from src.server import _PREVIEW_ARTICLES
        response = preview_client.get("/preview")
        for articles in _PREVIEW_ARTICLES.values():
            for article in articles:
                assert article["title"] in response.text

    def test_preview_has_no_newsletter_iframe_src(self, preview_client):
        response = preview_client.get("/preview")
        assert "/runs/preview/newsletter" not in response.text
