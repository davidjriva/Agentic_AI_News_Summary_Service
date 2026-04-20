# Clean Run Feature Design

**Date:** 2026-04-15
**Status:** Approved

## Overview

Add a "clean run" mode that clears only the last 12 hours of deduplication history before running the pipeline. This lets the pipeline re-fetch articles seen in that window — useful when a recent run missed articles or a fresh fetch is needed without wiping the full dedup cache.

Previously, `make run-clean` deleted all rows from `seen_articles` (full history wipe). The new behaviour scopes the deletion to the past 12 hours, consistent across the CLI, server, and Makefile.

---

## Components

### 1. `src/main.py` — `run_pipeline(clean: bool = False)`

- Add `clean: bool = False` parameter.
- When `True`, execute before `fetch_articles()`:
  ```sql
  DELETE FROM seen_articles WHERE seen_at >= datetime('now', '-12 hours')
  ```
- Log the deletion (e.g. `"[run_id] Clean run: cleared seen_articles for past 12 hours"`).
- Add `--clean` flag to `argparse` in `main()`, passed to `run_pipeline()`.

### 2. `src/server.py` — `POST /run`

- Add `clean: bool = False` as a FastAPI query parameter.
- Pass it to `run_pipeline(clean=clean)` inside the background thread.
- Concurrency guard (`_running_lock`, `_is_running`) is unchanged.
- Response shape is unchanged: `{"run_id": str}`.

### 3. `templates/dashboard.html.jinja2`

- Add a second "Clean Run" button in its own `<form method="POST" action="/run?clean=true">`.
- Both buttons disabled when `running` is `True`.
- Tooltips via `title` attribute:
  - **Run Now**: `"Fetch new articles not seen before and send the newsletter"`
  - **Clean Run**: `"Clears articles seen in the last 12 hours from the dedup cache, then runs the pipeline — use this if a recent run missed articles or you want a fresh fetch"`
- Clean Run button uses a visually secondary style (outlined) to distinguish it from Run Now.

### 4. `Makefile` — `run-clean`

Update to use the new `--clean` flag. The deletion is handled inside `run_pipeline()`, so no separate `sqlite3` call is needed:

```makefile
run-clean:
	poetry run python src/main.py --clean
```

---

## Data Flow

```
User clicks "Clean Run"
  → POST /run?clean=true
  → server.py: spawn background thread with run_pipeline(clean=True)
  → main.py: DELETE seen_articles WHERE seen_at >= now-12h
  → main.py: fetch_articles() (re-fetches recently-seen URLs)
  → ... rest of pipeline unchanged ...
```

---

## Out of Scope

- No changes to `fetcher.py`, `processor.py`, `ranker.py`, `renderer.py`, or `emailer.py`.
- No new database tables or schema changes.
- The 12-hour window is not configurable — it matches `LOOKBACK_HOURS` in spirit and is hardcoded for simplicity.
