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
                error TEXT
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

    def capture_run(run_id, dry_run):
        called_with["run_id"] = run_id
        called_with["dry_run"] = dry_run

    with patch("src.server.run_pipeline", side_effect=capture_run):
        response = client.post("/run")

    # Wait briefly for background thread to finish
    import time
    time.sleep(0.05)

    assert response.status_code == 200
    assert called_with.get("run_id") == response.json()["run_id"]
    assert called_with.get("dry_run") is False


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
    assert "Agentic AI News" in response.text


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
