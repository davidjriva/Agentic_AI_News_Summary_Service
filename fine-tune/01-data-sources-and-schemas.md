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

