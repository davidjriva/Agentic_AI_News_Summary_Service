"""Tests for src/server.py FastAPI endpoints."""

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_running_state():
    """Ensure _is_running is False before each test."""
    import src.server as srv
    srv._is_running = False
    yield
    srv._is_running = False


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Patch src.server.get_connection to use an isolated temp database."""
    db_path = tmp_path / "test_state.db"

    def _mock_get_connection():
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
                published_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS seen_articles (
                url TEXT PRIMARY KEY,
                seen_at TIMESTAMP
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
        conn.commit()
        return conn

    monkeypatch.setattr("src.server.get_connection", _mock_get_connection)

    # Initialize schema so tests can insert directly via sqlite3.connect
    init = _mock_get_connection()
    init.close()

    return db_path


@pytest.fixture()
def client(temp_db):
    from src.server import app
    return TestClient(app)


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


def test_list_runs_returns_records(client, temp_db):
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO runs (id, started_at, status, article_count) VALUES (?,?,?,?)",
        ("run-1", "2026-04-14T07:00:00", "success", 15),
    )
    conn.commit()
    conn.close()

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


def test_get_run_renders_detail_page(client, temp_db):
    html_content = "<html><body>Newsletter content</body></html>"
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO runs (id, started_at, status, article_count, html) VALUES (?,?,?,?,?)",
        ("run-2", "2026-04-14T07:00:00", "success", 10, html_content),
    )
    conn.commit()
    conn.close()

    response = client.get("/runs/run-2")
    assert response.status_code == 200
    assert "Articles by Source" in response.text
    assert "/runs/run-2/newsletter" in response.text


def test_get_run_error_status_returns_404(client, temp_db):
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO runs (id, started_at, status, error) VALUES (?,?,?,?)",
        ("run-3", "2026-04-14T07:00:00", "error", "Something went wrong"),
    )
    conn.commit()
    conn.close()

    response = client.get("/runs/run-3")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/newsletter (raw HTML)
# ---------------------------------------------------------------------------

def test_get_run_newsletter_returns_raw_html(client, temp_db):
    html_content = "<html><body>Newsletter content</body></html>"
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO runs (id, started_at, status, article_count, html) VALUES (?,?,?,?,?)",
        ("run-5", "2026-04-14T07:00:00", "success", 10, html_content),
    )
    conn.commit()
    conn.close()

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


def test_dashboard_shows_run_history(client, temp_db):
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO runs (id, started_at, status, article_count) VALUES (?,?,?,?)",
        ("run-4", "2026-04-14T07:00:00", "success", 12),
    )
    conn.commit()
    conn.close()

    response = client.get("/")
    assert response.status_code == 200
    assert "run-4" in response.text or "Success" in response.text


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------

class TestMetricsRoute:
    def test_metrics_renders(self, client, temp_db):
        """GET /metrics returns 200 with expected stat values in body."""
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO runs (id, started_at, completed_at, status, article_count) "
            "VALUES (?,?,?,?,?)",
            ("m-run-1", "2026-04-14T07:00:00", "2026-04-14T07:05:00", "success", 8),
        )
        conn.execute(
            "INSERT INTO run_articles (run_id, title, url, publication) VALUES (?,?,?,?)",
            ("m-run-1", "Test Article", "https://example.com/1", "TechCrunch"),
        )
        conn.commit()
        conn.close()

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


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/filtered
# ---------------------------------------------------------------------------

class TestFilteredArticlesEndpoint:
    def test_returns_empty_list_when_no_filtered_articles(self, client):
        response = client.get("/runs/nonexistent-run/filtered")
        assert response.status_code == 200
        assert response.json() == {"filtered": []}

    def test_returns_filtered_articles_for_run(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO runs (id, started_at, status, article_count) VALUES (?,?,?,?)",
            ("filt-run-1", "2026-04-14T07:00:00", "success", 5),
        )
        conn.execute(
            "INSERT INTO filtered_articles (run_id, url, title, publication, relevance_score, relevance_reason) "
            "VALUES (?,?,?,?,?,?)",
            (
                "filt-run-1",
                "https://example.com/dropped",
                "Dropped Article",
                "TechCrunch",
                3,
                "Not relevant to AI",
            ),
        )
        conn.commit()
        conn.close()

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

    def test_only_returns_articles_for_requested_run(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        for run_id in ("filt-run-a", "filt-run-b"):
            conn.execute(
                "INSERT INTO runs (id, started_at, status) VALUES (?,?,?)",
                (run_id, "2026-04-14T07:00:00", "success"),
            )
            conn.execute(
                "INSERT INTO filtered_articles (run_id, url, title, publication, relevance_score, relevance_reason) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, f"https://example.com/{run_id}", f"Article for {run_id}", "Source", 2, "Low relevance"),
            )
        conn.commit()
        conn.close()

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

    def test_returns_failed_articles_for_run(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO runs (id, started_at, status, article_count) VALUES (?,?,?,?)",
            ("fail-run-1", "2026-04-14T07:00:00", "success", 5),
        )
        conn.execute(
            "INSERT INTO failed_articles (run_id, url, title, publication, reason) "
            "VALUES (?,?,?,?,?)",
            (
                "fail-run-1",
                "https://example.com/failed",
                "Failed Article",
                "Wired",
                "LLM timeout",
            ),
        )
        conn.commit()
        conn.close()

        response = client.get("/runs/fail-run-1/failed")
        assert response.status_code == 200
        data = response.json()
        assert "failed" in data
        assert len(data["failed"]) == 1
        article = data["failed"][0]
        assert article["title"] == "Failed Article"
        assert article["publication"] == "Wired"
        assert article["reason"] == "LLM timeout"

    def test_only_returns_articles_for_requested_run(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        for run_id in ("fail-run-a", "fail-run-b"):
            conn.execute(
                "INSERT INTO runs (id, started_at, status) VALUES (?,?,?)",
                (run_id, "2026-04-14T07:00:00", "success"),
            )
            conn.execute(
                "INSERT INTO failed_articles (run_id, url, title, publication, reason) "
                "VALUES (?,?,?,?,?)",
                (run_id, f"https://example.com/{run_id}", f"Failed for {run_id}", "Source", "error"),
            )
        conn.commit()
        conn.close()

        response = client.get("/runs/fail-run-a/failed")
        assert response.status_code == 200
        data = response.json()
        assert len(data["failed"]) == 1
        assert data["failed"][0]["title"] == "Failed for fail-run-a"


# ---------------------------------------------------------------------------
# Dashboard dropped_count / failed_count columns
# ---------------------------------------------------------------------------

class TestDashboardDroppedFailedColumns:
    def test_dashboard_has_dropped_and_failed_headers(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO runs (id, started_at, status, article_count, dropped_count, failed_count) "
            "VALUES (?,?,?,?,?,?)",
            ("hdr-run-1", "2026-04-14T07:00:00", "success", 8, 2, 1),
        )
        conn.commit()
        conn.close()

        response = client.get("/")
        assert response.status_code == 200
        assert "Dropped" in response.text
        assert "Failed" in response.text

    def test_dashboard_shows_dropped_and_failed_counts(self, client, temp_db):
        conn = sqlite3.connect(str(temp_db))
        conn.execute(
            "INSERT INTO runs (id, started_at, status, article_count, dropped_count, failed_count) "
            "VALUES (?,?,?,?,?,?)",
            ("dash-run-1", "2026-04-14T07:00:00", "success", 10, 4, 2),
        )
        conn.commit()
        conn.close()

        response = client.get("/")
        assert response.status_code == 200
        assert "4" in response.text   # dropped_count
        assert "2" in response.text   # failed_count
