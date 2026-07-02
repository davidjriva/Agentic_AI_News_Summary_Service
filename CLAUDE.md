# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Tasks are run via [`just`](https://github.com/casey/just) (the old Makefile was removed). Run `just` with no args to list all recipes.

```bash
# Install dependencies
just install          # runs: poetry install

# Run the pipeline
just run              # full run (fetches, processes, ranks, sends email)
just run-dry          # dry run: skips email, prints HTML to stdout
just run-clean        # clears recent seen_articles, then runs

# Start the dashboard server
just serve            # uvicorn on http://localhost:8000 with --reload

# Start the local llama.cpp model server on :8089 (see Local LLM section)
just llama

# Run tests (DB-touching tests require Docker — see Testing approach)
just test                              # full suite
just test-one tests/test_processor.py  # single file or node

# Database migrations (Supabase Postgres via Alembic)
just migrate                       # alembic upgrade head
just makemigration "msg"           # autogenerate a migration from model changes

# Register launchd scheduling (macOS, 7 AM + 6 PM daily)
just install-launchd
```

## Architecture

The service is a linear pipeline triggered either by macOS launchd or `POST /run` on the FastAPI server:

```
fetcher.py → processor.py → ranker.py → renderer.py → emailer.py
```

`main.py:run_pipeline()` is the single entry point for both launchd (via CLI) and `server.py` (via background thread). Each stage receives and returns a `list[dict]` of articles; the dict schema is an additive contract — each stage appends fields without removing prior ones. The locked schema is documented in `src/config.py`.

### Module responsibilities

- **`src/db.py`** — Persistence is **Supabase Postgres** via SQLAlchemy. `get_session()` is a context manager yielding a `Session` from a process-wide pooled `Engine` (commits on clean exit, rolls back on error). ORM models live in **`src/models.py`**; the schema is owned by **Alembic** (`alembic/`), not created at runtime. The connection URL is built in `db.py` from `SUPABASE_DB_*` + `DB_PASSWORD` (escaped via `URL.create`), or overridden wholesale by `DATABASE_URL` (used by tests). Postgres-specific upserts (`ON CONFLICT`) are used for `seen_articles`/`article_scores`. A one-time `scripts/migrate_sqlite_to_pg.py` backfilled the legacy `data/state.db`.
- **`src/fetcher.py`** — Polls all `FEED_URLS`. Nine sources use `feedparser` for RSS/Atom; one (`HN_ALGOLIA_URL`) uses the Algolia JSON API via `requests`. Deduplication is read-then-batch-write per run — all new URLs are inserted at the end of `fetch_articles()`, not per article.
- **`src/processor.py`** — Calls one LLM request per article. Supports two providers via `LLM_PROVIDER` env var: `"anthropic"` (default, uses Anthropic SDK with ephemeral prompt caching on the system message) and `"local"` (llama.cpp via OpenAI-compatible `/v1/chat/completions`). Failures fall back to neutral scores (5/5) and truncated description as summary.
- **`src/ranker.py`** — Stateless scoring: `rank_score = (impact_score * 0.6) + (authenticity_score * 0.4)`. Does **not** assign a `rank` field (1-based ranking in the spec was never added; sort order is the implicit rank).
- **`src/renderer.py`** — Jinja2 templates in `templates/`. Returns `(html, plain_text)` tuple. `dashboard.html.jinja2` is served by the FastAPI root; `newsletter.html.jinja2` is the email body.
- **`src/server.py`** — FastAPI app; `_is_running` bool guards against concurrent runs via `threading.Lock`. `GET /runs/{run_id}` returns stored HTML directly from `runs.html` column.

### Configuration

All non-secret config lives in `src/config.py`. Secrets (`ANTHROPIC_API_KEY`, `GMAIL_APP_PASSWORD`, `GMAIL_SENDER`, `DB_PASSWORD`) and overrides (`LLM_PROVIDER`, `LOCAL_LLM_URL`, `LOCAL_LLM_MODEL`, `SUPABASE_DB_HOST`/`SUPABASE_DB_USER`/`SUPABASE_DB_PORT`/`SUPABASE_DB_NAME`, `DATABASE_URL`, `PORTFOLIO_BASE_URL`) are loaded from `.env` via `python-dotenv`. `.env.example` documents all env vars. The Supabase connection uses the **Session pooler** (port 5432).

### Newsletter subscriptions

The newsletter is sent to every `confirmed` row in the **`subscribers`** table (not a static `EMAIL_RECIPIENTS` list — that's no longer the send source). `emailer.py` sends **one message per subscriber** with a personalized unsubscribe link (`PORTFOLIO_BASE_URL/newsletter/unsubscribe?token=…`) + `List-Unsubscribe` one-click headers, isolating per-recipient failures. Each send is recorded in **`newsletter_deliveries`** (`sent`/`failed` per `run_id`). The public subscribe/confirm/unsubscribe routes live in the **portfolio** (Next.js) repo and hit Supabase with the `service_role` key — see `docs/portfolio-newsletter-integration.md`. Both new tables are RLS-enabled with no policies.

`RECIPIENTS` is populated from the `EMAIL_RECIPIENTS` env var (comma-separated); it is empty by default — the `.env` file must set it for email delivery to work.

### Local LLM (llama.cpp)

With `LLM_PROVIDER=local`, the processor POSTs to `{LOCAL_LLM_URL}/v1/chat/completions`. The dev setup serves **`news-agent-14b`** (a Qwen3-14B Q4_K_M build, system-prompted for this project) via llama.cpp's `llama-server` on `:8089` — start it with `just llama` (or the `llama-start` shell alias). The model is stored in **Ollama's blob store** and symlinked to `~/llama-cpp/models/news-agent-14b.gguf`, so llama.cpp serves the same weights without a separate download.

Operational gotchas on a 24 GB unified-memory Mac (learned the hard way):
- **Ollama and llama.cpp cannot both hold the 14B model** (~14 GB each in VRAM) — running both OOMs the Metal GPU. Unload Ollama first (`ollama stop news-agent-14b`); `just llama` does this automatically.
- **Use `-c 8192`, not 32768** — the large KV cache plus weights exceeds the GPU working-set cap and OOMs mid-decode. Once the Metal backend errors, the server is poisoned and must be restarted.
- **Throughput is ~9–10 tok/s** and Qwen3's "thinking" mode can emit ~470 tokens/call, so a full 75-article run is slow (tens of minutes to hours, worsened by thermal throttling on the fanless Air). The `article_scores` cache (TTL `SCORE_CACHE_TTL_DAYS`) makes reruns much faster.

### Testing approach

Tests mock at the boundary of each external dependency (`anthropic.Anthropic`, `requests.post`, `smtplib.SMTP`, `feedparser.parse`). The `src.config` module is patched directly via `patch.object(_cfg, "LLM_PROVIDER", ...)` rather than environment variable patching.

DB-touching tests use a **real, ephemeral Postgres** via `testcontainers` (the models rely on Postgres-specific upserts, so SQLite can't stand in). The session-scoped container + per-test clean live in `tests/conftest.py`; request the `db` fixture to use it. **These tests require a running Docker daemon.** Pure-logic tests (ranker, renderer, filter, and the LLM paths in processor/fetcher) stay mock-based and never touch the DB — the autouse `_isolate_db` fixture in `test_processor.py` keeps them off Postgres. CI (`.github/workflows/ci.yml`) runs the full suite; Docker is preinstalled on the runner.
