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

## Future Improvements

> These are explicitly deferred and not in scope for the initial implementation.

1. **Scale email delivery** — swap `emailer.py` for a transactional ESP (SendGrid, Resend, Postmark) when the recipient list exceeds 50 addresses. The `emailer.py` interface is designed to make this a drop-in replacement.

2. **Cloud hosting with managed cron** — migrate from macOS launchd to AWS Lambda + EventBridge Scheduler, or Google Cloud Run + Cloud Scheduler, or Railway. `main.py` is written as a stateless function to make this straightforward. State store would move from local SQLite to a managed DB (e.g., PlanetScale, Supabase).

3. **Social signal sources** — add Twitter/X (via paid API) or scraping-based sources (Apify) as optional fetcher plugins once RSS coverage proves insufficient.

4. **Web UI / archive** — store past newsletters and serve them via a simple read-only web interface.
