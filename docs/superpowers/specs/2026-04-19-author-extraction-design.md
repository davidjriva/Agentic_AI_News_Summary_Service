# Author Extraction — Design Spec

**Date:** 2026-04-19
**Status:** Approved

## Problem

The `author` field is frequently blank in the newsletter because many RSS feeds (e.g., thenewstack.io) don't populate `<author>` or `<dc:creator>` tags, even when the article page itself lists an author. The byline renders as a bare "By" with nothing after it.

## Goal

Maximize author coverage using only data already in the pipeline (feed title + description), with no new HTTP requests and no extra API calls.

## Approach: Two-Stage Extraction

### Stage 1 — Regex in `fetcher.py`

A new helper `_extract_author(description: str) -> str` is called inside `_entry_to_dict` when `feedparser` returns an empty `entry.author`.

It matches common byline patterns in the RSS description text:
- `By Alex Wilhelm`
- `Written by Sarah Chen-Moore`
- `— Name` at line start
- `| Name` separator style

The regex captures 2–4 title-cased words. Returns `""` on no match.

`_process_hn` and `_process_langchain` are unaffected — HN provides `author` directly from the API; LangChain scrapes it from HTML.

### Stage 2 — LLM fallback in `processor.py`

`SUMMARY_SYSTEM_PROMPT` is extended to include `author` as an optional output field alongside `summary`:

> "If an author name is clearly identifiable from the title or description, return it as `author`; otherwise return an empty string."

In `summarize_articles`, after parsing the LLM JSON response:
- If `article["author"]` is blank **and** the LLM returned a non-empty `author`, apply it.
- If both are blank, leave the field empty — no author is invented.
- Existing `summary` behavior is unchanged.

No separate API call is made. Author extraction piggybacks the summarization call that already runs for every article.

## Data Flow

```
RSS feed entry
  └─ feedparser.entry.author populated?
       ├─ yes → use as-is
       └─ no  → _extract_author(description)
                  ├─ regex match → use result
                  └─ no match   → author = ""
                                    └─ summarize_articles LLM call
                                         ├─ LLM returns author → apply if article.author blank
                                         └─ LLM returns ""     → leave blank
```

## Constraints

- Feed-only: no article page fetches.
- No extra LLM calls: author extraction piggybacks the summary call.
- No Pydantic structured outputs: consistent with existing `json.loads()` pattern (noted as future improvement).
- `_process_hn` and `_process_langchain` untouched.

## Files Changed

| File | Change |
|------|--------|
| `src/fetcher.py` | Add `_extract_author()` helper; call it in `_entry_to_dict` when author is blank |
| `src/processor.py` | Extend `SUMMARY_SYSTEM_PROMPT` to return `author`; apply in `summarize_articles` |

## Testing

- Unit tests for `_extract_author` covering: explicit byline match, written-by match, no match, empty string input.
- `summarize_articles` test: when article author is blank and LLM returns an author, it is applied.
- `summarize_articles` test: when article already has an author, LLM-returned author does not overwrite it.
