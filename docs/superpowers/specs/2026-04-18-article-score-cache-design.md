# Article Score Cache & Dedup Scope Change

**Date:** 2026-04-18
**Status:** Approved

## Problem

Every article that enters the pipeline triggers an LLM call to compute 6 score fields (~8 s/article). When the same URL re-appears — via `--clean` runs, dev/test DB resets, or articles surfacing again after the 7-day prune — scores are recomputed unnecessarily.

Additionally, the current fetcher marks *all* fetched URLs as seen, preventing lower-ranked articles from re-appearing in future runs even when they were never included in a newsletter.

## Goals

1. Skip LLM scoring for any article whose URL has a valid cached score entry.
2. Narrow deduplication to only URLs that were actually sent in a newsletter (top 10).
3. Expand the article lookback window from 24 h to 72 h (3 days).

## Non-Goals

- Caching summaries (ephemeral; generated fresh for whatever top-10 lands each run).
- Changing the summary generation flow in `summarize_articles()`.

---

## Database Schema

New table added in `db.py:get_connection()`:

```sql
CREATE TABLE IF NOT EXISTS article_scores (
    url                 TEXT PRIMARY KEY,
    impact_score        INTEGER,
    authenticity_score  INTEGER,
    relevance_score     INTEGER,
    impact_reason       TEXT,
    authenticity_reason TEXT,
    relevance_reason    TEXT,
    cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Pruning runs alongside `seen_articles` in `fetch_articles()`:

```sql
DELETE FROM article_scores WHERE cached_at < datetime('now', '-3 days');
```

---

## Expiration Policy

| Concern | TTL | Pruned in |
|---|---|---|
| `seen_articles` (sent URLs only) | 7 days | `fetch_articles()` |
| `article_scores` (cached scores) | 3 days | `fetch_articles()` |
| `LOOKBACK_HOURS` | 72 h | `config.py` |

TTL is intentionally aligned with `LOOKBACK_HOURS`: any article fetchable in the current run could have a valid cache entry from a prior run within the same window.

---

## Component Changes

### `src/config.py`

- `LOOKBACK_HOURS: 24 → 72`

### `src/db.py`

- Add `_CREATE_ARTICLE_SCORES` DDL and execute it in `get_connection()`.

### `src/fetcher.py`

- Remove all writes to `seen_articles` (the `new_urls` batch insert at the end of `fetch_articles()`).
- Add `article_scores` TTL prune alongside the existing `seen_articles` prune.
- `fetch_articles()` return type is unchanged (`list[dict]`); it no longer has a side-effect on `seen_articles`.

### `src/processor.py`

- At the start of `process_articles()`, load all non-expired `article_scores` rows into a `dict[str, dict]` keyed by URL (one query, not N).
- In `_process_one()`: if the article URL is in the cache dict, return cached scores immediately without calling the LLM.
- On LLM success: `INSERT OR REPLACE INTO article_scores` with the 6 score fields and current timestamp.

### `src/main.py`

- After `rank_articles()` returns, insert the top-10 article URLs into `seen_articles` (replacing the removed write in `fetch_articles()`).

---

## Data Flow

```
fetch_articles()
  → prune seen_articles (>7 days) + article_scores (>3 days)
  → return all articles within 72h lookback, skipping only seen_articles URLs

process_articles()
  → load cache: SELECT * FROM article_scores WHERE cached_at >= now - 3 days
  → per article: cache hit → attach scores; cache miss → LLM call → INSERT OR REPLACE article_scores

filter_articles() → rank_articles()

main.py
  → INSERT top-10 URLs into seen_articles

summarize_articles() → render → send
```

---

## Testing

- Existing tests mock `anthropic.Anthropic` and `requests.post`; score cache tests should mock `get_connection()` or use an in-memory SQLite DB.
- Key cases:
  - Cache hit: LLM not called, cached scores attached to article.
  - Cache miss: LLM called, result written to `article_scores`.
  - Expired entry (> 3 days): treated as cache miss.
  - `fetch_articles()` no longer writes to `seen_articles`.
  - `main.py` writes exactly top-10 URLs to `seen_articles` after ranking.
