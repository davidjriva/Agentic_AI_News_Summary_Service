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

