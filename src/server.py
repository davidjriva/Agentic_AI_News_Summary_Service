"""FastAPI dashboard and pipeline trigger server."""

import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.db import get_connection
from src.main import run_pipeline

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

app = FastAPI(title="Agentic AI News Dashboard")

_running_lock = threading.Lock()
_is_running: bool = False


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Serve the run-history dashboard."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, started_at, completed_at, status, article_count, error "
        "FROM runs ORDER BY started_at DESC"
    ).fetchall()
    conn.close()
    runs = [dict(row) for row in rows]
    return templates.TemplateResponse(
        request,
        "dashboard.html.jinja2",
        {"runs": runs, "running": _is_running},
    )


@app.post("/run")
def trigger_run() -> dict:
    """Start the pipeline in a background thread.

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
            run_pipeline(run_id=run_id, dry_run=False)
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
        "SELECT id, started_at, completed_at, status, article_count, error "
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
        "SELECT publication, title, url FROM run_articles WHERE run_id=? ORDER BY publication, title",
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
        sources.setdefault(pub, []).append({"title": r["title"], "url": r["url"]})

    return templates.TemplateResponse(
        request,
        "run_detail.html.jinja2",
        {"run_id": run_id, "started_at": row["started_at"], "sources": sources},
    )


@app.get("/status")
def status() -> dict:
    """Return whether a pipeline run is currently in progress."""
    return {"running": _is_running}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
