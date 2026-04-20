# Data sources & dataset schemas

This document defines what data exists today, what’s missing, and the target dataset formats for training.

---

## What’s persisted today (source of truth: `data/state.db`)

Per run, the pipeline currently persists:

- **`runs.html`** (`runs` table): rendered newsletter HTML for the run.
  - Contains only the **top `TOP_N`** articles (after relevance filtering + ranking + source diversity caps).
- **`run_articles`**: the same top `TOP_N` article metadata for the run (URL/title/publication/etc.).
- **`filtered_articles`**: articles dropped due to relevance threshold.
- **`failed_articles`**: LLM-processing failures.
- **`article_scores`**: cached scores/reasons keyed by URL (not per run).

Important implication:

- Articles that were **kept by relevance but didn’t make top `TOP_N`** are **not persisted per run** today.

---

## Missing persistence (to enable RLHF/DPO at scale)

If you want feedback and preference training beyond the top `TOP_N`, you need to persist additional “candidate pool” data per run (see `fine-tune/03-feedback-loop-and-dpo.md`).

At minimum, add tables:

- `run_article_candidates` — what the human actually saw (draft + metadata)
- `article_feedback` — upvote/downvote + short reason note
- `article_edits` — your rewrite paired with the original draft

Optional but recommended if you want broader preference data:

- `run_article_pool` (or extend `run_article_candidates`) to store **all kept-by-relevance** articles before `TOP_N` truncation.

---

## Required output schema (the thing you want the model to emit)

All training targets should be consistent with this:

```json
{
  "summary": "Newsletter-ready summary paragraph.",
  "author": "Jane Doe"
}
```

---

## SFT dataset format (JSONL)

Each line is one training example:

```json
{
  "instruction": "Summarize this AI research for a technical audience.",
  "input": "RAW_TEXT_HERE",
  "output": { "summary": "…", "author": "…" }
}
```

Notes:

- `input` should be **full extracted article text** when available; otherwise fall back to metadata/snippets.
- Keep `output` as the strict object above (not freeform prose) to reinforce structure.

---

## Preference dataset formats (for DPO / “RLHF-style” training)

There are multiple conventions depending on training code. The core concept is the same:

- **prompt**: the conditioning input (article text + instruction)
- **chosen**: the preferred model output
- **rejected**: the less preferred model output

Your highest-signal preference source will be **draft vs edited**:

- `chosen` = your edited final summary
- `rejected` = the model draft you saw

---

## Subagent tasks (implementation-ready)

### Task DS1 — Validate current DB schema vs docs

- **Where**: `src/db.py` and `data/state.db`
- **Work**:
  - Confirm tables/columns exist and match the “persisted today” list.
  - Confirm that `run_articles` contains only top `TOP_N` (it does today; verify by reading `src/main.py`).
- **Acceptance**:
  - A short diff/notes list of any mismatches between code and docs.

### Task DS2 — Define new tables as SQL (authoritative schema)

- **Deliverable**: exact `CREATE TABLE IF NOT EXISTS ...` SQL strings for:
  - `run_article_candidates`
  - `article_feedback`
  - `article_edits`
- **Constraints**:
  - Must include `run_id` + `url` columns and index them.
  - `article_feedback.reason` must be present (short note).
  - Include a uniqueness strategy (e.g. unique `(run_id, url)` for candidates, and upsert behavior for votes).


