"""FastAPI dashboard and pipeline trigger server."""

import json
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import src.config as _cfg
from src.db import get_connection
from src.main import run_pipeline

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_PREVIEW_ARTICLES: dict[str, list[dict]] = {
    "Anthropic Blog": [
        {"title": "Claude 4 Achieves State-of-the-Art on Long-Context Reasoning", "url": "#", "rank_score": 9.8},
        {"title": "Introducing Constitutional AI v2: Safer by Default", "url": "#", "rank_score": 8.4},
    ],
    "OpenAI Blog": [
        {"title": "GPT-5 Technical Report: Multimodal Capabilities and Alignment", "url": "#", "rank_score": 9.2},
        {"title": "DALL-E 4 Released with Real-Time Video Generation", "url": "#", "rank_score": 7.6},
    ],
    "ArXiv": [
        {"title": "Scaling Laws for Mixture-of-Experts Language Models", "url": "#", "rank_score": 8.8},
        {"title": "Self-Play Fine-Tuning Converts Weak to Strong Language Models", "url": "#", "rank_score": 7.2},
        {"title": "RoPE Scaling Methods for Long-Context LLMs: A Survey", "url": "#", "rank_score": 5.8},
    ],
    "MIT Technology Review": [
        {"title": "AI Regulation in 2026: What the EU Act Means for Developers", "url": "#", "rank_score": 6.4},
        {"title": "The Hidden Carbon Cost of Training Large Language Models", "url": "#", "rank_score": 5.2},
    ],
    "Hacker News": [
        {"title": "Ask HN: What is your current local LLM setup in 2026?", "url": "#", "rank_score": 4.8},
        {"title": "Llama 4 70B runs at 120 tok/s on a single RTX 5090", "url": "#", "rank_score": 6.0},
        {"title": "Show HN: Open-source tool for LLM prompt versioning", "url": "#", "rank_score": 4.0},
    ],
}

app = FastAPI(title="Agentic AI News Dashboard")

_running_lock = threading.Lock()
_is_running: bool = False


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Serve the run-history dashboard."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, started_at, completed_at, status, article_count, error, "
        "dropped_count, failed_count "
        "FROM runs ORDER BY started_at DESC"
    ).fetchall()
    conn.close()
    runs = [dict(row) for row in rows]
    return templates.TemplateResponse(
        request,
        "dashboard.html.jinja2",
        {"runs": runs, "running": _is_running, "active": "dashboard"},
    )


@app.get("/preview", response_class=HTMLResponse)
def preview(request: Request) -> HTMLResponse:
    """Render the run detail page with fake articles for UI development."""
    return templates.TemplateResponse(
        request,
        "run_detail.html.jinja2",
        {
            "run_id": "preview",
            "started_at": "Preview Mode",
            "sources": _PREVIEW_ARTICLES,
            "preview": True,
        },
    )


@app.post("/run")
def trigger_run(clean: bool = False) -> dict:
    """Start the pipeline in a background thread.

    Args:
        clean: If True, delete seen_articles from the past 12 hours before fetching.

    Returns:
        {"run_id": str} — the ID of the new run.

    Raises:
        HTTPException(409) if a run is already in progress.
    """
    global _is_running

    with _running_lock:
        if _is_running:
            raise HTTPException(status_code=409, detail="A run is already in progress")
        _is_running = True

    run_id = str(uuid.uuid4())

    def _background():
        global _is_running
        try:
            run_pipeline(run_id=run_id, dry_run=False, clean=clean)
        finally:
            _is_running = False

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()

    return {"run_id": run_id}


@app.get("/runs")
def list_runs() -> list[dict]:
    """Return metadata for all past runs, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, started_at, completed_at, status, article_count, error, "
        "dropped_count, failed_count "
        "FROM runs ORDER BY started_at DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.get("/runs/{run_id}/newsletter", response_class=HTMLResponse)
def get_run_newsletter(run_id: str) -> HTMLResponse:
    """Return the raw rendered HTML newsletter for a completed run.

    Raises:
        HTTPException(404) if run_id is unknown or the run did not succeed.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT html, status FROM runs WHERE id=?", (run_id,)
    ).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "success":
        raise HTTPException(status_code=404, detail="Run has no HTML output")

    return HTMLResponse(content=row["html"])


@app.get("/runs/{run_id}/filtered")
def get_filtered_articles(run_id: str) -> dict:
    """Return filtered (dropped) articles for a run.

    Returns:
        {"filtered": list[dict]} — articles dropped due to low relevance score.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT url, title, publication, relevance_score, relevance_reason "
        "FROM filtered_articles WHERE run_id = ? ORDER BY relevance_score DESC",
        (run_id,),
    ).fetchall()
    conn.close()
    return {"filtered": [dict(r) for r in rows]}


@app.get("/runs/{run_id}/failed")
def get_failed_articles(run_id: str) -> dict:
    """Return failed (LLM-processing error) articles for a run.

    Returns:
        {"failed": list[dict]} — articles that failed LLM processing.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT url, title, publication, reason, failed_at "
        "FROM failed_articles WHERE run_id = ? ORDER BY failed_at DESC",
        (run_id,),
    ).fetchall()
    conn.close()
    return {"failed": [dict(r) for r in rows]}


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def get_run(request: Request, run_id: str) -> HTMLResponse:
    """Render the run detail page: newsletter iframe + articles-by-source sidebar.

    Raises:
        HTTPException(404) if run_id is unknown or the run did not succeed.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT status, started_at FROM runs WHERE id=?", (run_id,)
    ).fetchone()
    source_rows = conn.execute(
        "SELECT publication, title, url, rank_score FROM run_articles WHERE run_id=? ORDER BY publication, title",
        (run_id,),
    ).fetchall()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if row["status"] != "success":
        raise HTTPException(status_code=404, detail="Run has no HTML output")

    sources: dict[str, list[dict]] = {}
    for r in source_rows:
        pub = r["publication"] or "Unknown"
        sources.setdefault(pub, []).append({"title": r["title"], "url": r["url"], "rank_score": r["rank_score"]})

    return templates.TemplateResponse(
        request,
        "run_detail.html.jinja2",
        {"run_id": run_id, "started_at": row["started_at"], "sources": sources},
    )


@app.get("/metrics", response_class=HTMLResponse)
def metrics(request: Request) -> HTMLResponse:
    """Serve the lifetime metrics dashboard."""
    conn = get_connection()

    articles_seen = conn.execute("SELECT COUNT(*) FROM seen_articles").fetchone()[0]
    total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    successful_runs = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status='success'"
    ).fetchone()[0]

    status_rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM runs GROUP BY status"
    ).fetchall()
    status_counts = {row["status"]: row["count"] for row in status_rows}

    source_rows = conn.execute(
        "SELECT publication, COUNT(*) as count FROM run_articles "
        "GROUP BY publication ORDER BY 2 DESC"
    ).fetchall()

    run_rows = conn.execute(
        "SELECT started_at, article_count FROM runs WHERE status='success' "
        "ORDER BY started_at ASC LIMIT 20"
    ).fetchall()

    conn.close()

    subscribers = len(_cfg.RECIPIENTS)
    emails_delivered = successful_runs * subscribers

    return templates.TemplateResponse(
        request,
        "metrics.html.jinja2",
        {
            "subscribers": subscribers,
            "emails_delivered": emails_delivered,
            "total_runs": total_runs,
            "articles_seen": articles_seen,
            "sources_labels_json": json.dumps(
                [row["publication"] for row in source_rows]
            ),
            "sources_data_json": json.dumps([row["count"] for row in source_rows]),
            "run_status_labels_json": json.dumps(list(status_counts.keys())),
            "run_status_data_json": json.dumps(list(status_counts.values())),
            "run_dates_json": json.dumps(
                [row["started_at"][:10] for row in run_rows]
            ),
            "run_counts_json": json.dumps([row["article_count"] for row in run_rows]),
            "active": "metrics",
        },
    )


@app.get("/status")
def status() -> dict:
    """Return whether a pipeline run is currently in progress."""
    return {"running": _is_running}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
