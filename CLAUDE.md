# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
make install          # runs: poetry install

# Run the pipeline
make run              # full run (fetches, processes, ranks, sends email)
make run-dry          # dry run: skips email, prints HTML to stdout

# Start the dashboard server
make serve            # uvicorn on http://localhost:8000 with --reload

# Run tests
make test             # poetry run pytest tests/ -v
poetry run pytest tests/test_processor.py -v   # single test file
poetry run pytest tests/test_processor.py::TestLocalLLMProvider -v  # single class

# Register launchd scheduling (macOS, 7 AM + 6 PM daily)
make install-launchd
```

## Architecture

The service is a linear pipeline triggered either by macOS launchd or `POST /run` on the FastAPI server:

```
fetcher.py → processor.py → ranker.py → renderer.py → emailer.py
```

`main.py:run_pipeline()` is the single entry point for both launchd (via CLI) and `server.py` (via background thread). Each stage receives and returns a `list[dict]` of articles; the dict schema is an additive contract — each stage appends fields without removing prior ones. The locked schema is documented in `src/config.py`.

### Module responsibilities

- **`src/db.py`** — `get_connection()` opens SQLite at `data/state.db` and auto-creates both tables (`seen_articles`, `runs`) on first call. Every caller is responsible for closing the connection.
- **`src/fetcher.py`** — Polls all `FEED_URLS`. Nine sources use `feedparser` for RSS/Atom; one (`HN_ALGOLIA_URL`) uses the Algolia JSON API via `requests`. Deduplication is read-then-batch-write per run — all new URLs are inserted at the end of `fetch_articles()`, not per article.
- **`src/processor.py`** — Calls one LLM request per article. Supports two providers via `LLM_PROVIDER` env var: `"anthropic"` (default, uses Anthropic SDK with ephemeral prompt caching on the system message) and `"local"` (llama.cpp via OpenAI-compatible `/v1/chat/completions`). Failures fall back to neutral scores (5/5) and truncated description as summary.
- **`src/ranker.py`** — Stateless scoring: `rank_score = (impact_score * 0.6) + (authenticity_score * 0.4)`. Does **not** assign a `rank` field (1-based ranking in the spec was never added; sort order is the implicit rank).
- **`src/renderer.py`** — Jinja2 templates in `templates/`. Returns `(html, plain_text)` tuple. `dashboard.html.jinja2` is served by the FastAPI root; `newsletter.html.jinja2` is the email body.
- **`src/server.py`** — FastAPI app; `_is_running` bool guards against concurrent runs via `threading.Lock`. `GET /runs/{run_id}` returns stored HTML directly from `runs.html` column.

### Configuration

All non-secret config lives in `src/config.py`. Secrets (`ANTHROPIC_API_KEY`, `GMAIL_APP_PASSWORD`, `GMAIL_SENDER`) and optional overrides (`LLM_PROVIDER`, `LOCAL_LLM_URL`, `LOCAL_LLM_MODEL`, `EMAIL_RECIPIENTS`) are loaded from `.env` via `python-dotenv`. `.env.example` documents all env vars.

`RECIPIENTS` is populated from the `EMAIL_RECIPIENTS` env var (comma-separated); it is empty by default — the `.env` file must set it for email delivery to work.

### Testing approach

Tests mock at the boundary of each external dependency (`anthropic.Anthropic`, `requests.post`, `smtplib.SMTP`, `feedparser.parse`). The `src.config` module is patched directly via `patch.object(_cfg, "LLM_PROVIDER", ...)` rather than environment variable patching.
