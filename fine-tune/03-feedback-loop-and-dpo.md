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

### `article_feedback` (vote + note)

- `id` (INTEGER PK)
- `run_id` (TEXT, indexed)
- `url` (TEXT, indexed)
- `vote` (INTEGER) — `+1` or `-1`
- `reason` (TEXT) — short optional note explaining the vote
- `created_at` (TIMESTAMP default current)

### `article_edits` (edits become SFT)

- `id` (INTEGER PK)
- `run_id` (TEXT, indexed)
- `url` (TEXT, indexed)
- `original_text` (TEXT)
- `edited_text` (TEXT)
- `created_at` (TIMESTAMP default current)

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

