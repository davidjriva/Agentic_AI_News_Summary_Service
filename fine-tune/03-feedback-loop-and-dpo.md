# Feedback loop (upvote/downvote + note + edits) and DPO export

This document describes the “lightweight RLHF-style loop”: collecting feedback in the dashboard and turning it into training data.

---

## Goal

Capture feedback during normal review:

- **Vote**: upvote/downvote
- **Reason note**: a short “why I felt that way”
- **Edits**: optional rewrite of the summary

This produces:

- **SFT examples** from edits (draft → edited)
- **Preference pairs** for DPO (chosen vs rejected)

---

## What needs to be persisted (SQLite)

### `run_article_candidates` (snapshot what the human saw)

Stores the candidate state at review time so exports are stable even if upstream sources change.

- `id` (INTEGER PK)
- `run_id` (TEXT, indexed)
- `url` (TEXT, indexed)
- `title` (TEXT)
- `publication` (TEXT)
- `published_at` (TEXT)
- `model_summary_draft` (TEXT)
- `model_prompt_version` (TEXT, optional)
- `created_at` (TIMESTAMP default current)

Accuracy note:

- Today, your pipeline only generates summaries for the top `TOP_N`. If you want feedback on a larger candidate pool, persist candidates **before** the ranking truncation.

Suggested authoritative SQL (to paste into `src/db.py`):

```sql
CREATE TABLE IF NOT EXISTS run_article_candidates (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id             TEXT NOT NULL,
  url                TEXT NOT NULL,
  title              TEXT,
  publication        TEXT,
  published_at       TEXT,
  model_summary_draft TEXT,
  model_prompt_version TEXT,
  created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_run_article_candidates_run_id ON run_article_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_run_article_candidates_url ON run_article_candidates(url);
```

### `article_feedback` (vote + note)

- `id` (INTEGER PK)
- `run_id` (TEXT, indexed)
- `url` (TEXT, indexed)
- `vote` (INTEGER) — `+1` or `-1`
- `reason` (TEXT) — short optional note explaining the vote
- `created_at` (TIMESTAMP default current)

Suggested SQL:

```sql
CREATE TABLE IF NOT EXISTS article_feedback (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  url        TEXT NOT NULL,
  vote       INTEGER NOT NULL, -- +1 or -1
  reason     TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, url)
);
CREATE INDEX IF NOT EXISTS idx_article_feedback_run_id ON article_feedback(run_id);
CREATE INDEX IF NOT EXISTS idx_article_feedback_url ON article_feedback(url);
```

### `article_edits` (edits become SFT)

- `id` (INTEGER PK)
- `run_id` (TEXT, indexed)
- `url` (TEXT, indexed)
- `original_text` (TEXT)
- `edited_text` (TEXT)
- `created_at` (TIMESTAMP default current)

Suggested SQL:

```sql
CREATE TABLE IF NOT EXISTS article_edits (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  url           TEXT NOT NULL,
  original_text TEXT,
  edited_text   TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_article_edits_run_id ON article_edits(run_id);
CREATE INDEX IF NOT EXISTS idx_article_edits_url ON article_edits(url);
```

---

## Minimal API surface (FastAPI)

- `POST /feedback/vote` body: `{run_id, url, vote, reason?}`
- `POST /feedback/edit` body: `{run_id, url, edited_text}`
- `GET /feedback/run/{run_id}` returns all feedback for the run

---

## Dashboard UX (lightweight)

Per article:

- Upvote button
- Downvote button
- Note field (inline or modal) saved alongside the vote
- Edit action (textarea) to rewrite the summary

---

## Exporting training data

### SFT-from-edits (highest signal)

For each `article_edits` row:

- `chosen_output` = post-processed `{"summary": edited_text, "author": ...}`
- `input` reconstructed by URL refetching (preferred) or metadata fallback

### DPO preference pairs

Two pragmatic pairings:

1. **Draft vs edited**:
   - `chosen` = edited
   - `rejected` = draft
2. **Included vs relevance-dropped** (coarser):
   - `chosen` = newsletter-included
   - `rejected` = relevance-dropped

---

## Subagent tasks (implementation-ready)

### Task FB1 — Add new tables to DB bootstrap/migrations

- **Where**: `src/db.py` in `get_connection()` alongside existing `_CREATE_*` strings
- **Work**:
  - Add the three `CREATE TABLE IF NOT EXISTS ...` blocks above.
  - Ensure it’s safe to run repeatedly (idempotent).
- **Acceptance**:
  - Starting the app creates the new tables automatically in `data/state.db`

### Task FB2 — Persist candidates for the current top `TOP_N` (MVP)

- **Where**: `src/main.py` after `summarize_articles()` and before `runs.html` is stored
- **Work**:
  - Insert (run_id, url, title, publication, published_at, model_summary_draft) for each article in the curated set
  - Use `INSERT OR IGNORE` or `INSERT ... ON CONFLICT(run_id,url) DO UPDATE` semantics to make it safe
- **Acceptance**:
  - After a run, querying `run_article_candidates` for that `run_id` returns ~`TOP_N` rows

### Task FB3 — Implement feedback API endpoints

- **Where**: `src/server.py`
- **Routes**:
  - `POST /feedback/vote` → upsert into `article_feedback`
  - `POST /feedback/edit` → insert into `article_edits` (and optionally also upsert vote)
  - `GET /feedback/run/{run_id}` → return candidates + feedback + edits for UI hydration
- **Acceptance**:
  - Requests succeed and persist to SQLite
  - `vote` is validated to be `+1` or `-1`

### Task FB4 — Add UI controls to run detail page

- **Where**: `templates/run_detail.html.jinja2` (this is the page you use to review a run)
- **Work**:
  - Add per-article controls:
    - upvote / downvote buttons
    - small note input
    - edit textarea modal
  - Wire with `fetch()` calls to the API routes
- **Acceptance**:
  - You can vote + add a note and refresh the page and see persisted state (via `GET /feedback/run/{run_id}`)


