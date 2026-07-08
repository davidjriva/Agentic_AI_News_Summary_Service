# Scoring Rubric + Two-Stage LLM Efficiency — Design

**Date:** 2026-07-07
**Status:** Approved design, pending implementation plan

## Problem

Two issues with the current pipeline:

1. **Efficiency.** The scoring pass makes one full-rubric LLM call per fetched
   article (~78/run), sending the complete untruncated description (arXiv
   abstracts run 1–2K tokens) and asking for a 6-field JSON. On the local
   14B model this is 35–45s per article — 85%+ of total runtime — and roughly
   half of those articles are then dropped by the relevance gate, making their
   impact/authenticity scores wasted work. JSON parse failures burn full
   retries (up to 2 × ~40s).

2. **Scoring quality.** `impact_score` — weighted 0.6 in ranking, the most
   influential number — has no rubric at all in the system prompt.
   `relevance_score` is only a pass/fail gate (≥ `RELEVANCE_THRESHOLD`) and
   never influences rank order. There are no calibration anchors, so the
   small local model clusters scores in the 6–8 band, flattening the ranking
   signal.

A secondary trap: `article_scores` is cached by URL alone with a 3-day TTL,
so any rubric change silently mixes old-rubric and new-rubric scores in one
ranking for up to 3 days.

Note (corrected during analysis): the summary phase is **not** wasteful —
`rank_articles()` already truncates to `TOP_N=10`, so `summarize_articles()`
only ever sees the top 10.

## Decisions (made with user)

- **Scoring pass restructure:** two-stage triage **plus** per-call tightening.
- **Ranking:** relevance joins the formula — `impact×0.5 + relevance×0.3 + authenticity×0.2`.
- **Validation:** small golden-set eval (~15–20 hand-labeled articles, `just eval`).
- **Constraint:** no eval runs / LLM calls executed while the currently
  in-flight pipeline run is using the llama.cpp server.

## Design

### 1. Pipeline restructure — two-stage scoring

New `main.py` flow:

```
fetch → triage (relevance only, ALL articles) → gate (filter.py, unchanged)
      → full scoring (impact + authenticity, SURVIVORS only) → rank
      → seen-mark → summarize top-10 → render → email
```

`processor.py` splits `process_articles()` into:

- **`triage_articles(articles, run_id)`** — one short call per article
  returning `relevance_score` + `relevance_reason` only. Failure after
  retries → dead-letter to `failed_articles` (an article without a relevance
  score cannot be gated; same semantics as today).
- **`score_articles(articles, run_id)`** — `impact_score`,
  `authenticity_score` + reasons, run only on gate survivors (~40–50% of
  fetch). Relevance fields from triage are carried forward on the article
  dict, so the additive schema contract in `config.py` is unchanged
  downstream.

`filter.py` logic is untouched; it just runs between the two stages.
`FilteredArticle` rows are written from triage output as today (relevance
fields only — the model already allows NULL impact/auth).

### 2. Call tightening

- Descriptions truncated **per stage**, not a single flat cap. Measured feed
  data shows only arXiv descriptions exceed ~400 chars (arXiv median ~1,430,
  p90 ~1,905; The Verge ~345; TechCrunch ~141; HuggingFace empty), so the cap
  only ever bites arXiv abstracts.
  - **Triage (relevance): 500 chars.** Relevance is a topic judgment settled
    by the title + lead sentences, and this pass runs on *all* fetched
    articles, so token savings matter most here.
  - **Scoring (impact/authenticity): 1,900 chars** (≈ the full arXiv abstract;
    arXiv hard-caps abstracts at 1,920 chars). The impact-signaling claims
    ("SOTA", "first to…", result magnitudes) live in the abstract's tail, and
    classification signal is known to sit at both the head and tail of a
    document (Sun et al. 2019, *How to Fine-Tune BERT for Text
    Classification?*). This pass runs only on the ~40–50% of articles that
    survive the gate, and for every non-arXiv source the larger cap is a no-op.
    Cost: ~355 extra prefill tokens (~4 chars/token, per OpenAI's token
    guidance) on a minority of articles — negligible against per-call decode.
  - Implemented via a `max_desc` argument on the shared `_build_user_content`;
    triage passes 500, scoring passes 1,900.
- Local provider calls add **`response_format: json_schema`** so llama.cpp
  grammar-constrains decoding to the exact response schema — malformed JSON
  becomes impossible, eliminating parse-failure retries. The Anthropic path
  keeps its current parse-and-retry.
- `max_tokens`: triage 128, full scoring 512 (down from 1024). Summary
  unchanged (512).

### 3. Rubric redesign

Two system prompts replace the single `SYSTEM_PROMPT`:

- **Triage prompt** — keeps the existing relevance rubric verbatim; asks only
  for `relevance_score` + `relevance_reason`.
- **Scoring prompt** — impact + authenticity with anchored bands:
  - Impact: 9–10 frontier releases / field-moving research; 7–8 notable
    models, tools, significant papers; 5–6 incremental research, ecosystem
    news; 3–4 routine business news; 1–2 negligible.
  - Authenticity: same criteria as today but with numeric bands (9–10 primary
    source / peer-reviewed … 3–4 anonymous or aggregator content).
  - Explicit calibration line: *"Most articles score 4–6; reserve 8+ for
    genuinely field-moving news."* — counteracts 6–8 clustering.

### 4. Ranking formula

`rank_score = impact×0.5 + relevance×0.3 + authenticity×0.2`. The ≥6 gate
stays. Updated in `ranker.py`, the schema doc block in `config.py`, and tests.

### 5. Cache versioning

Two new Text columns on `article_scores` via Alembic migration —
`triage_version` and `score_version` — each a short hash of that stage's
system prompt computed at import time. Cache reads require the matching
stage's version, so prompt changes automatically invalidate stale entries
instead of mixing scoring regimes within the TTL window. One column per
stage (rather than a single combined hash) means a triage-prompt change
doesn't needlessly invalidate cached impact/auth scores, and vice versa.

Two-stage caching on the same row: triage upserts relevance fields +
`triage_version` (impact/auth stay NULL); full scoring upserts impact/auth +
`score_version` into the row. The full-scoring cache check additionally
requires `impact_score IS NOT NULL`.

### 6. Golden-set eval

- `eval/golden_articles.json` — ~15–20 real articles pulled from recent
  `run_articles`/`filtered_articles` rows, each with hand-agreed expected
  score bands (±1) for relevance/impact/authenticity.
- `scripts/eval_prompts.py` + `just eval` recipe — scores the golden set
  through the configured provider; reports mean absolute error per dimension
  and gate-decision agreement. No DB writes. Reusable for future prompt
  changes.
- **Not executed** until the in-flight pipeline run finishes (shares the
  llama.cpp server).

### 7. Error handling & testing

- Failure semantics unchanged: triage/scoring failures dead-letter; summary
  failures fall back to the cleaned RSS description.
- Tests (mocked at `requests.post` / `anthropic.Anthropic` boundaries, per
  existing convention):
  - triage returns relevance-only fields; scoring preserves triage fields
  - gate runs between stages (main.py orchestration)
  - local calls include `response_format` json_schema and truncated description
  - ranker: new weights, tie-break, source-diversity unchanged
  - cache: version filtering and two-stage upsert (testcontainers `db` fixture)
  - eval script scoring math (pure logic, no network)

## Expected effect

Cold cache, local 14B: today ~78 full-rubric calls at 35–45s (~50+ min).
After: 78 short triage calls (~5–8s) + ~35 tightened full calls + 10
summaries ≈ 15–25 min — roughly **2.5–3.5× faster**, with more rubric
guidance on the articles that matter and none spent on dropped ones.

## Out of scope

- Batch scoring (multiple articles per call) — rejected for coarse failure
  granularity and positional-bias risk on a 14B model.
- Anthropic-path structured output (tool use) — parse-and-retry is fine there.
- Feed list, fetcher, renderer, emailer changes.
