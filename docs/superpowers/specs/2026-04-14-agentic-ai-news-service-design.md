# Agentic AI News Summary Service — Design Spec
**Date:** 2026-04-14  
**Status:** Approved

---

## Overview

A Python service that runs twice daily via macOS launchd, pulls the latest Agentic AI news from RSS/Atom feeds, uses Claude API to summarize and rank articles by impact and authenticity (including author credibility), then delivers an HTML newsletter via Gmail SMTP to a small list of recipients.

---

## Architecture

```
                        ┌─────────────────┐
                        │  launchd (cron)  │
                        └────────┬────────┘
                                 │
                    ┌────────────▼────────────┐
                    │       main.py            │◄── server.py (POST /run)
                    │   (pipeline orchestrator)│
                    └────────────┬────────────┘
                                 │ in-memory dicts
              ┌──────────────────▼──────────────────┐
              │            fetcher.py                │
              │  RSS/Atom feeds → filtered articles  │
              └──────────────────┬──────────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │           processor.py               │
              │  Claude API → summary + scores       │
              └──────────────────┬──────────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │            ranker.py                 │
              │  composite score → top N articles    │
              └──────────────────┬──────────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │           renderer.py                │
              │  ranked articles → HTML newsletter   │
              └────────┬─────────────────┬──────────┘
                       │                 │
         ┌─────────────▼──┐    ┌─────────▼──────────┐
         │   emailer.py   │    │  state.db (runs)    │
         │  Gmail SMTP    │    │  stored for web UI  │
         └────────────────┘    └─────────┬───────────┘
                                         │
                               ┌─────────▼───────────┐
                               │     server.py        │
                               │  GET /runs/{id}      │
                               │  dashboard display   │
                               └─────────────────────┘
```

Data passes between pipeline stages as Python lists/dicts in memory. No intermediate files are written during a normal run. Completed run HTML is persisted to `state.db` for the web UI. `make run-dry` skips email send and prints the newsletter to stdout.

---

## Modules

### `fetcher.py`
- Polls all configured RSS/Atom feed URLs
- Fetches up to `MAX_ARTICLES_PER_SOURCE` (default 10) items per feed
- Filters articles to those published within the last `LOOKBACK_HOURS` (default 12)
- Deduplicates against `state.db` (SQLite table of seen URLs) to avoid re-processing across runs
- Extracts: `title`, `url`, `description`, `author`, `publication`, `published_at`
- Returns a list of raw article dicts; expect ~30–60 articles per run across 10 sources

### `processor.py`
- Receives all articles that passed the fetcher's time + dedup filter (~30–60 per run)
- Calls Claude API once per article with a structured prompt
- Default model: `claude-haiku-4-5` (~$3–6/month at this volume); swap to `claude-sonnet-4-6` in config for higher-quality summaries (~3× cost)
- Prompt includes: `author`, `publication`, `title`, `description snippet`
- Claude returns structured JSON per article:
  ```json
  {
    "summary": "2-3 sentence summary",
    "impact_score": 8,
    "authenticity_score": 7,
    "impact_reason": "...",
    "authenticity_reason": "..."
  }
  ```
- Authenticity scoring rubric:
  - Is the author a known researcher, practitioner, or credible journalist?
  - Is the publication a primary source (lab blog, company announcement) or secondary (commentary, aggregator)?
  - Is the content original research/reporting or opinion/repost?
  - Unknown/anonymous authors score conservatively

### `ranker.py`
- Computes composite score: `rank_score = (impact_score * 0.6) + (authenticity_score * 0.4)`
- Sorts articles descending by `rank_score`
- Keeps top N articles (configurable, default 15)

### `renderer.py`
- Builds HTML email from ranked article list
- Each article card shows: rank position, headline (linked), publication, author, date, summary, impact/authenticity scores
- Includes plain-text fallback for email clients that don't render HTML
- Newsletter header shows run date/time and total sources scanned

### `emailer.py`
- Sends rendered newsletter via Gmail SMTP (port 587, STARTTLS)
- Auth: Gmail App Password (not account password)
- Recipients loaded from `config.py`
- Subject line: `Agentic AI Digest — {date} {AM|PM}`

### `main.py`
- Orchestrates the full pipeline in order
- Logs start/end of each stage with article counts
- Catches and logs per-stage errors without crashing the full run
- Exits with non-zero code on total failure (for launchd to detect)
- Designed to be called both by launchd (CLI) and by `server.py` (web trigger)

### `server.py`
- Lightweight **FastAPI** web server; run with `make serve` or `python src/server.py`
- Serves a local dashboard at `http://localhost:8000`
- Endpoints:
  - `POST /run` — triggers the pipeline in a background thread; returns run ID
  - `GET /runs` — lists past runs (timestamp, article count, status)
  - `GET /runs/{run_id}` — returns the rendered HTML newsletter for that run
  - `GET /status` — returns whether a run is currently in progress
- Persists run results (rendered HTML + metadata) in `state.db` table `runs`
- No auth required (localhost only)

### `config.py`
Non-secret configuration:
- `FEED_URLS`: list of RSS feed URLs
- `RECIPIENTS`: list of email addresses
- `TOP_N`: number of articles to include in newsletter (default 15)
- `MAX_ARTICLES_PER_SOURCE`: max articles fetched per feed before time filtering (default 10)
- `CLAUDE_MODEL`: model ID (default `claude-haiku-4-5`; swap to `claude-sonnet-4-6` for higher quality)
- `LOOKBACK_HOURS`: how far back to fetch articles (default 12)

### `.env`
Secrets (gitignored):
- `ANTHROPIC_API_KEY`
- `GMAIL_APP_PASSWORD`
- `GMAIL_SENDER`

---

## RSS Feed Sources

| Source | Feed URL | Signal Type |
|---|---|---|
| Hacker News (AI) | `https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI` | Community signal |
| arXiv cs.AI | `https://arxiv.org/rss/cs.AI` | Research papers |
| TechCrunch AI | `https://techcrunch.com/tag/artificial-intelligence/feed` | Industry news |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed` | Industry news |
| MIT Technology Review AI | `https://www.technologyreview.com/topic/artificial-intelligence/feed` | Deep analysis |
| The Verge AI | `https://www.theverge.com/ai-artificial-intelligence/rss/index.xml` | Consumer/product |
| Wired AI | `https://www.wired.com/tag/artificial-intelligence/feed/rss` | Long-form analysis |
| Anthropic Blog | `https://www.anthropic.com/news/rss.xml` | Primary source |
| Google News RSS | `https://news.google.com/rss/search?q=agentic+AI` | Broad coverage |
| Reddit r/MachineLearning | `https://www.reddit.com/r/MachineLearning/.rss` | Community signal |

---

## API Keys & Credentials

| Credential | Source | Cost |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | ~$3–6/month (Haiku) or ~$9–18/month (Sonnet) |
| `GMAIL_APP_PASSWORD` | Google Account → Security → 2FA → App Passwords | Free |

No other credentials required.

---

## Scheduling (macOS launchd)

- Trigger times: **7:00 AM** and **6:00 PM** daily
- A `launchd` `.plist` template is included in the repo at `deploy/com.agenticnews.service.plist`
- A `make install` command registers the plist with launchd and loads it
- Logs written to `~/Library/Logs/AgenticNewsService/`

---

## State & Deduplication

- SQLite database at `data/state.db`
- Table `seen_articles(url TEXT PRIMARY KEY, seen_at TIMESTAMP)`
- Records are retained for 7 days, then pruned, to handle re-publication edge cases

---

## Project Structure

```
Agentic_AI_News_Summary_Service/
├── src/
│   ├── fetcher.py
│   ├── processor.py
│   ├── ranker.py
│   ├── renderer.py
│   ├── emailer.py
│   ├── main.py
│   ├── server.py
│   └── config.py
├── templates/
│   ├── newsletter.html.jinja2    # email + run storage template
│   └── dashboard.html.jinja2    # web UI dashboard
├── data/
│   └── state.db          (gitignored)
├── deploy/
│   └── com.agenticnews.service.plist
├── tests/
│   ├── test_fetcher.py
│   ├── test_processor.py
│   ├── test_ranker.py
│   ├── test_renderer.py
│   └── test_server.py
├── .env.example
├── .env                  (gitignored)
├── requirements.txt
├── Makefile
└── README.md
```

---

## Dependencies

Managed with **Poetry** (`pyproject.toml` + `poetry.lock`).

Runtime:
```
anthropic          # Claude API (with prompt caching)
feedparser         # RSS/Atom parsing
python-dotenv      # .env loading
jinja2             # HTML email + dashboard templating
requests           # HTTP for Algolia/Google News
fastapi            # web server for dashboard + manual trigger
uvicorn            # ASGI server for FastAPI
sqlite3            # stdlib — state store
smtplib            # stdlib — email sending
```

Dev:
```
pytest
httpx              # FastAPI test client
```

---

## Testing

Development follows **TDD**: tests are written before each module, implementation makes them pass.

- Unit tests for each module using `pytest`
- `test_fetcher.py`: mock HTTP responses, verify deduplication logic
- `test_processor.py`: mock Claude API responses, verify JSON parsing
- `test_ranker.py`: verify scoring formula and top-N cutoff
- `test_renderer.py`: verify HTML output contains expected fields
- `test_server.py`: verify `/run`, `/runs`, `/runs/{id}`, `/status` endpoints using FastAPI test client
- Integration test: `make run-dry` runs the full pipeline but skips email send, printing the newsletter to stdout instead
- Manual UI test: `make serve` → open `http://localhost:8000`, click "Run Now", verify results appear

---

## Parallel Execution Plan

Implementation is split into three sequential phases. Phases 1 and 3 are done by the coordinating agent. Phase 2 dispatches five independent Ralph-loop subagents concurrently.

---

### Phase 1 — Pre-work (coordinating agent, sequential)

Must complete before any subagent is dispatched. These artifacts are shared contracts; if agents define them independently, conflicts are guaranteed.

**1. Scaffold project structure**
Create all directories and empty placeholder files so agents have a stable tree to work in:
```
src/, tests/, templates/, data/, deploy/
```

**2. Write `src/config.py`**
All constants in one place. No agent should touch this file.
```python
FEED_URLS = [...]           # 10 sources from spec
RECIPIENTS = ["davidjriva@gmail.com"]
TOP_N = 15
MAX_ARTICLES_PER_SOURCE = 10
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
LOOKBACK_HOURS = 12
```

**3. Write `.env.example`**
```
ANTHROPIC_API_KEY=
GMAIL_APP_PASSWORD=
GMAIL_SENDER=
```

**4. Write `pyproject.toml` (Poetry) and install deps**
All runtime and dev deps as listed in the Dependencies section.

**5. Write `Makefile`**
Targets: `run`, `run-dry`, `serve`, `test`, `install`.

**6. Write `deploy/com.agenticnews.service.plist`**
launchd plist for 7:00 AM and 6:00 PM daily triggers.

**7. Lock the article dict schema**
Every agent must use these exact field names. Document as a comment in `src/config.py`:
```python
# Article dict schema — fields produced and consumed by pipeline stages:
# Produced by fetcher.py:
#   title: str
#   url: str              — primary dedup key
#   description: str      — raw snippet from feed
#   author: str           — empty string if unknown
#   publication: str      — human-readable feed source name
#   published_at: str     — ISO 8601 datetime string
# Added by processor.py:
#   summary: str
#   impact_score: int     — 1–10
#   authenticity_score: int — 1–10
#   impact_reason: str
#   authenticity_reason: str
# Added by ranker.py:
#   rank_score: float
#   rank: int             — 1-based position after sort
```

**8. Initialize `data/state.db` with schema**
```sql
CREATE TABLE IF NOT EXISTS seen_articles (
    url TEXT PRIMARY KEY,
    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT,       -- 'running' | 'success' | 'error'
    article_count INTEGER,
    html TEXT,
    error TEXT
);
```

---

### Phase 2 — Parallel subagents (five Ralph-loop agents, concurrent)

Dispatch all five at the same time. Each agent runs a Ralph loop until its module and tests are complete and all tests pass. Agents must not modify `config.py`, `state.db` schema, `main.py`, or `server.py`.

Each agent prompt must include:
- The article dict schema from Phase 1, step 7
- The `config.py` contents
- The `state.db` schema
- The module's section from this spec
- TDD instruction: write tests first, then implementation

---

#### Agent A — `fetcher.py`

**Owns:** `src/fetcher.py`, `tests/test_fetcher.py`

**Goal:** Implement RSS/Atom feed fetching with time filtering and deduplication.

**Inputs:** `config.FEED_URLS`, `config.LOOKBACK_HOURS`, `config.MAX_ARTICLES_PER_SOURCE`, `data/state.db` (`seen_articles` table)

**Output:** `list[dict]` — articles matching the locked schema (fetcher fields only: `title`, `url`, `description`, `author`, `publication`, `published_at`)

**Key behaviors to implement and test:**
- Fetch each feed URL; use `feedparser` for RSS/Atom, `requests` + JSON for Algolia/Google News
- Filter to articles published within `LOOKBACK_HOURS`
- Skip any URL already in `seen_articles`
- Insert newly fetched URLs into `seen_articles`
- Prune `seen_articles` records older than 7 days
- Return up to `MAX_ARTICLES_PER_SOURCE` articles per source before time filtering

**Test coverage:**
- Mock HTTP responses (use `unittest.mock` or `pytest-mock`)
- Verify time filtering rejects old articles
- Verify deduplication skips seen URLs
- Verify deduplication inserts new URLs
- Verify 7-day pruning

---

#### Agent B — `processor.py`

**Owns:** `src/processor.py`, `tests/test_processor.py`

**Goal:** Call Claude API once per article; parse structured JSON response.

**Inputs:** `list[dict]` from fetcher (fetcher fields), `config.CLAUDE_MODEL`, `ANTHROPIC_API_KEY` from env

**Output:** Same list with processor fields added to each dict (`summary`, `impact_score`, `authenticity_score`, `impact_reason`, `authenticity_reason`)

**Key behaviors to implement and test:**
- Use the `anthropic` SDK with prompt caching (cache the system prompt across calls)
- Send one API call per article; include `author`, `publication`, `title`, `description` in prompt
- Parse Claude's JSON response; handle malformed JSON gracefully (skip article, log warning)
- Validate scores are integers 1–10; clamp or skip if out of range
- Authenticity rubric (see Modules section above) must be embedded in the system prompt

**Test coverage:**
- Mock `anthropic.Anthropic` client
- Verify fields are added correctly for a valid response
- Verify malformed JSON is handled without crashing
- Verify out-of-range scores are handled
- Verify prompt caching headers are set

---

#### Agent C — `ranker.py`

**Owns:** `src/ranker.py`, `tests/test_ranker.py`

**Goal:** Score, sort, and truncate the processed article list.

**Inputs:** `list[dict]` from processor (fetcher + processor fields), `config.TOP_N`

**Output:** Same list, sorted descending by `rank_score`, truncated to `TOP_N`, with `rank` and `rank_score` fields added

**Key behaviors to implement and test:**
- Composite score: `rank_score = (impact_score * 0.6) + (authenticity_score * 0.4)`
- Sort descending by `rank_score`
- Assign `rank` as 1-based position after sort
- Truncate to `TOP_N`; if fewer articles than `TOP_N`, return all

**Test coverage:**
- Verify scoring formula correctness
- Verify sort order
- Verify `rank` field assignment
- Verify top-N truncation
- Verify behavior when input list is shorter than `TOP_N`

---

#### Agent D — `renderer.py` + templates

**Owns:** `src/renderer.py`, `templates/newsletter.html.jinja2`, `templates/dashboard.html.jinja2`, `tests/test_renderer.py`

**Goal:** Render ranked articles into an HTML newsletter and a plain-text fallback.

**Inputs:** `list[dict]` from ranker (all fields), run metadata (`run_date`, `run_time`, `sources_scanned`)

**Output:** `dict` with keys `html: str` and `plain_text: str`

**Key behaviors to implement and test:**
- Use Jinja2 to render `newsletter.html.jinja2`
- Each article card: rank, headline (linked to `url`), publication, author, `published_at`, summary, impact score, authenticity score
- Newsletter header: run date/time, total sources scanned
- `dashboard.html.jinja2`: list of past runs (timestamp, article count, status) with link to each run's HTML
- Plain-text fallback: rank, title, url, summary per article
- Subject line helper: `f"Agentic AI Digest — {date} {AM_or_PM}"`

**Test coverage:**
- Verify HTML contains expected field values
- Verify linked headlines use `url` field
- Verify header shows correct run metadata
- Verify plain-text output contains title and url
- Verify subject line format

---

#### Agent E — `emailer.py`

**Owns:** `src/emailer.py`, `tests/test_emailer.py`

**Goal:** Send the rendered newsletter via Gmail SMTP.

**Inputs:** `renderer` output (`html`, `plain_text`), subject line string, `config.RECIPIENTS`, `GMAIL_SENDER` and `GMAIL_APP_PASSWORD` from env

**Output:** None (side effect: email sent); raises on failure

**Key behaviors to implement and test:**
- Connect to `smtp.gmail.com:587` with STARTTLS
- Authenticate with `GMAIL_SENDER` / `GMAIL_APP_PASSWORD`
- Build `MIMEMultipart('alternative')` message with plain-text and HTML parts
- Send to all `RECIPIENTS` in a single SMTP call
- Raise a descriptive exception on auth failure or send failure

**Test coverage:**
- Mock `smtplib.SMTP`
- Verify STARTTLS is called
- Verify correct credentials used
- Verify both MIME parts attached
- Verify all recipients addressed
- Verify exception raised on SMTP error

---

### Phase 3 — Integration (coordinating agent, sequential)

After all five agents report completion and their tests pass:

**1. Write `src/main.py`**
- Import and call each stage in order: `fetcher → processor → ranker → renderer → emailer`
- Log article count at start/end of each stage
- Catch per-stage exceptions; log and continue where possible; exit non-zero on total failure
- Accept `--dry-run` flag: skip `emailer`, print HTML to stdout
- Persist run result (HTML + metadata) to `runs` table in `state.db`
- Return run ID for use by `server.py`

**2. Write `src/server.py` + `tests/test_server.py`**
- FastAPI app with endpoints: `POST /run`, `GET /runs`, `GET /runs/{run_id}`, `GET /status`
- `POST /run` triggers `main.py` pipeline in a background thread; returns `{"run_id": "..."}`
- `GET /runs` reads from `runs` table; returns list of run metadata
- `GET /runs/{run_id}` returns stored HTML for that run
- `GET /status` returns `{"running": true/false}`
- Serve `dashboard.html.jinja2` at `GET /`

**3. Run full test suite**
```bash
make test   # all unit tests must pass
```

**4. Run integration test**
```bash
make run-dry   # full pipeline, no email send; verify HTML printed to stdout
```

**5. Verify `make serve`**
Open `http://localhost:8000`, trigger a run, verify results appear in dashboard.

**6. Run `make install`** to register launchd plist and verify scheduling.

---

## Future Improvements

> These are explicitly deferred and not in scope for the initial implementation.

1. **Scale email delivery** — swap `emailer.py` for a transactional ESP (SendGrid, Resend, Postmark) when the recipient list exceeds 50 addresses. The `emailer.py` interface is designed to make this a drop-in replacement.

2. **Cloud hosting with managed cron** — migrate from macOS launchd to AWS Lambda + EventBridge Scheduler, or Google Cloud Run + Cloud Scheduler, or Railway. `main.py` is written as a stateless function to make this straightforward. State store would move from local SQLite to a managed DB (e.g., PlanetScale, Supabase).

3. **Social signal sources** — add Twitter/X (via paid API) or scraping-based sources (Apify) as optional fetcher plugins once RSS coverage proves insufficient.

4. **Web UI / archive** — store past newsletters and serve them via a simple read-only web interface.
