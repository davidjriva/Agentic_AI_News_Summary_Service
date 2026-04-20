# Bootstrapped SFT export (from existing runs)

This document covers how to generate a first SFT dataset *without* hand-curation by reusing artifacts you already store.

---

## What you can export today (without schema changes)

You can export up to **~10 examples per run** from:

- `runs.html` (rendered newsletter) → extract URL + summary text

Limitation:

- `runs.html` includes only the top `TOP_N` articles, so this export is **high-signal but limited volume**.

---

## Planned export script

Script: `fine-tune/export_sft_dataset.py`

Inputs:

- `data/state.db`
- (optionally) a URL fetcher + full-text extractor for reconstructing `input`

Outputs:

- `fine-tune/data/sft.train.jsonl`
- `fine-tune/data/sft.eval.jsonl`

Suggested flags:

- `--db data/state.db`
- `--out-dir fine-tune/data/`
- `--max-examples 500`
- `--since-days 30`
- `--min-summary-chars 120`
- `--require-input-source fulltext|rss|any`
- `--dedupe-by-url`

---

## Input reconstruction strategy (best → fallback)

For each `(url, output)` pair extracted from the newsletter:

1. **Full-text extraction (preferred)**:
   - Fetch `url`
   - Extract main content
   - Truncate to a safe max length
2. **RSS/metadata fallback**:
   - title + publication + author (if known) + description/snippet
3. **Skip** if neither is available and `--require-input-source` disallows fallback.

---

## Quality filters (automatic)

- Output length within a reasonable band (example: 300–1,500 chars).
- Exclude boilerplate-heavy outputs (“Click here”, nav text).
- Exclude empty/near-empty inputs.

---

## Stable train/eval split

Use a deterministic split based on a URL hash so successive exports are comparable.

---

## Subagent tasks (implementation-ready)

### Task EX1 — Implement `fine-tune/export_sft_dataset.py` (MVP: newsletter-only)

- **Where**: new file `fine-tune/export_sft_dataset.py`
- **Inputs**:
  - SQLite DB at `data/state.db`
  - `runs` table: `id`, `started_at`, `status`, `html`
- **Work**:
  - Select successful runs: `SELECT id, started_at, html FROM runs WHERE status='success' ORDER BY started_at DESC`
  - Parse `html` to extract:
    - article URL (`<a href="...">` in the newsletter template)
    - summary text (the paragraph that contains `article.summary`)
  - Write JSONL lines with:
    - `instruction` (constant)
    - `input` (MVP can be a structured metadata string; full-text reconstruction can be added in EX2)
    - `output` as `{"summary": extracted_summary, "author": ""}` (author blank for MVP unless you also parse byline)
- **Acceptance**:
  - Running the script produces non-empty `fine-tune/data/sft.train.jsonl` and `fine-tune/data/sft.eval.jsonl`
  - All lines are valid JSON
  - `output` is always an object with keys `summary` and `author`

### Task EX2 — Add input reconstruction via URL fetch + main-text extraction

- **Where**: extend `fine-tune/export_sft_dataset.py`
- **Work**:
  - Fetch each `url` (respect timeouts and user-agent)
  - Extract main content (best-effort). If extraction fails, fall back to a metadata-only input.
  - Add flags:
    - `--max-input-chars`
    - `--require-input-source fulltext|any`
- **Acceptance**:
  - For a sample of URLs, `input` contains substantive article text (not nav chrome)
  - Script completes without crashing on a single bad URL

### Task EX3 — Deterministic split + de-dupe

- **Work**:
  - De-dupe by URL across runs (keep newest output)
  - Stable split by hashing URL (e.g. first byte < threshold → eval)
- **Acceptance**:
  - Re-running export yields identical train/eval membership for the same URLs

