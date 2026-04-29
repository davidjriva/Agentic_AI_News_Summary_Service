"""FastAPI dashboard and pipeline trigger server."""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import src.config as _cfg
from src.db import get_connection
from src.main import run_pipeline
from src.renderer import render_newsletter


def _format_duration(started_at: str | None, completed_at: str | None) -> str:
    """Return human-readable duration string from ISO timestamp strings."""
    if not started_at or not completed_at:
        return ""
    try:
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in started_at else "%Y-%m-%dT%H:%M:%S"
        fmt2 = "%Y-%m-%dT%H:%M:%S.%f" if "." in completed_at else "%Y-%m-%dT%H:%M:%S"
        s = datetime.fromisoformat(started_at.replace("Z", ""))
        e = datetime.fromisoformat(completed_at.replace("Z", ""))
        diff = max(0, int((e - s).total_seconds()))
        if diff >= 60:
            return f"{diff // 60}m {diff % 60}s"
        return f"{diff}s"
    except Exception:
        return ""

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

_PREVIEW_NEWSLETTER_ARTICLES: list[dict] = [
    {
        "title": "Claude 4 Achieves State-of-the-Art on Long-Context Reasoning",
        "url": "#", "publication": "Anthropic Blog",
        "published_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
        "author": "Anthropic Research", "rank_score": 9.8,
        "impact_score": 10, "authenticity_score": 9, "relevance_score": 10,
        "summary": "Anthropic's Claude 4 sets new benchmarks on long-context reasoning tasks, outperforming all prior models on the SCROLLS and ZeroScrolls evaluation suites with a 200K-token context window.",
    },
    {
        "title": "GPT-5 Technical Report: Multimodal Capabilities and Alignment",
        "url": "#", "publication": "OpenAI Blog",
        "published_at": datetime(2026, 4, 16, tzinfo=timezone.utc),
        "author": "OpenAI", "rank_score": 9.2,
        "impact_score": 9, "authenticity_score": 10, "relevance_score": 10,
        "summary": "OpenAI releases the full technical report for GPT-5, detailing its native multimodal architecture, RLHF improvements, and a new alignment technique that reduces harmful outputs by 40% vs. GPT-4.",
    },
    {
        "title": "Scaling Laws for Mixture-of-Experts Language Models",
        "url": "#", "publication": "ArXiv",
        "published_at": datetime(2026, 4, 15, tzinfo=timezone.utc),
        "author": "Various", "rank_score": 8.8,
        "impact_score": 9, "authenticity_score": 8, "relevance_score": 9,
        "summary": "Researchers derive new scaling laws for MoE architectures, showing that optimal expert count scales as a power law of total parameter count, with implications for efficient training of frontier models.",
    },
    {
        "title": "Introducing Constitutional AI v2: Safer by Default",
        "url": "#", "publication": "Anthropic Blog",
        "published_at": datetime(2026, 4, 14, tzinfo=timezone.utc),
        "author": "Anthropic Safety Team", "rank_score": 8.4,
        "impact_score": 8, "authenticity_score": 9, "relevance_score": 10,
        "summary": "Constitutional AI v2 introduces self-critique chains that run at inference time, allowing models to detect and revise harmful outputs without additional human labeling.",
    },
    {
        "title": "Self-Play Fine-Tuning Converts Weak to Strong Language Models",
        "url": "#", "publication": "ArXiv",
        "published_at": datetime(2026, 4, 13, tzinfo=timezone.utc),
        "author": "Various", "rank_score": 7.2,
        "impact_score": 7, "authenticity_score": 8, "relevance_score": 8,
        "summary": "A new fine-tuning method using self-play — where the model generates both prompts and responses — consistently improves reasoning benchmarks without any curated dataset.",
    },
    {
        "title": "DALL-E 4 Released with Real-Time Video Generation",
        "url": "#", "publication": "OpenAI Blog",
        "published_at": datetime(2026, 4, 12, tzinfo=timezone.utc),
        "author": "OpenAI", "rank_score": 7.6,
        "impact_score": 8, "authenticity_score": 7, "relevance_score": 8,
        "summary": "DALL-E 4 adds real-time video generation at 24fps, temporal consistency across frames, and a new prompt adherence score that reduces hallucinated scene elements by 60%.",
    },
    {
        "title": "AI Regulation in 2026: What the EU Act Means for Developers",
        "url": "#", "publication": "MIT Technology Review",
        "published_at": datetime(2026, 4, 11, tzinfo=timezone.utc),
        "author": "MIT Tech Review Staff", "rank_score": 6.4,
        "impact_score": 6, "authenticity_score": 7, "relevance_score": 7,
        "summary": "A practical breakdown of the EU AI Act's high-risk system classifications and what compliance looks like for teams building customer-facing LLM products in 2026.",
    },
    {
        "title": "Llama 4 70B runs at 120 tok/s on a single RTX 5090",
        "url": "#", "publication": "Hacker News",
        "published_at": datetime(2026, 4, 10, tzinfo=timezone.utc),
        "author": "community", "rank_score": 6.0,
        "impact_score": 6, "authenticity_score": 6, "relevance_score": 7,
        "summary": "Community benchmarks show Llama 4 70B achieving 120 tokens/second on a single RTX 5090 using llama.cpp with Q4_K_M quantization, making frontier-class local inference practical for developers.",
    },
    {
        "title": "RoPE Scaling Methods for Long-Context LLMs: A Survey",
        "url": "#", "publication": "ArXiv",
        "published_at": datetime(2026, 4, 9, tzinfo=timezone.utc),
        "author": "Various", "rank_score": 5.8,
        "impact_score": 6, "authenticity_score": 5, "relevance_score": 8,
        "summary": "A comprehensive survey of rotary position embedding (RoPE) scaling techniques, covering linear interpolation, YaRN, LongRoPE, and their trade-offs on context extension tasks.",
    },
    {
        "title": "The Hidden Carbon Cost of Training Large Language Models",
        "url": "#", "publication": "MIT Technology Review",
        "published_at": datetime(2026, 4, 8, tzinfo=timezone.utc),
        "author": "MIT Tech Review Staff", "rank_score": 5.2,
        "impact_score": 5, "authenticity_score": 6, "relevance_score": 6,
        "summary": "An investigation into the energy and water consumption of frontier model training runs, with new estimates suggesting GPT-5 training consumed the equivalent of 10,000 US households' annual electricity.",
    },
    {
        "title": "Ask HN: What is your current local LLM setup in 2026?",
        "url": "#", "publication": "Hacker News",
        "published_at": datetime(2026, 4, 7, tzinfo=timezone.utc),
        "author": "community", "rank_score": 4.8,
        "impact_score": 4, "authenticity_score": 6, "relevance_score": 6,
        "summary": "A popular Hacker News thread collecting community setups for running LLMs locally, featuring responses covering hardware choices, quantization strategies, and use-case fit.",
    },
    {
        "title": "Show HN: Open-source tool for LLM prompt versioning",
        "url": "#", "publication": "Hacker News",
        "published_at": datetime(2026, 4, 6, tzinfo=timezone.utc),
        "author": "community", "rank_score": 4.0,
        "impact_score": 4, "authenticity_score": 4, "relevance_score": 6,
        "summary": "A new open-source tool for tracking prompt changes with Git-like semantics, including diff views, rollback, and evaluation hooks for regression testing prompt edits.",
    },
]

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
    for run in runs:
        run["duration_str"] = _format_duration(run.get("started_at"), run.get("completed_at"))
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


@app.get("/preview/newsletter", response_class=HTMLResponse)
def preview_newsletter() -> HTMLResponse:
    """Render the newsletter template with fake articles for UI development."""
    html, _ = render_newsletter(
        _PREVIEW_NEWSLETTER_ARTICLES,
        datetime(2026, 4, 17, 7, 0, tzinfo=timezone.utc),
    )
    return HTMLResponse(content=html)


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
    result = []
    for row in rows:
        d = dict(row)
        d["duration_str"] = _format_duration(d.get("started_at"), d.get("completed_at"))
        result.append(d)
    return result


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
