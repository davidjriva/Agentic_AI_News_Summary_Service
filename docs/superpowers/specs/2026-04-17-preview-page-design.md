# Preview Page Design

**Date:** 2026-04-17  
**Status:** Approved

## Problem

The `rank_score` field is now displayed per article in the run detail sidebar, but verifying the UI requires running the full pipeline each time. A `/preview` route with hardcoded fake articles removes that friction during development.

## Approach

Add a `GET /preview` route to `server.py` that renders the existing `run_detail.html.jinja2` template with a `preview=True` flag and hardcoded fake data. No new template file is created — all preview-specific behavior is gated on the `preview` flag inside the existing template.

## Route

`GET /preview` in `src/server.py`:

- Passes `preview=True`, `run_id="preview"`, `started_at="Preview Mode"`, and a `sources` dict
- `sources` is a hardcoded constant `PREVIEW_ARTICLES` — ~15 fake AI news articles across 4–5 publications with `title`, `url`, and `rank_score` spanning the full range (~4.0–9.8)
- No DB access

## Template Changes (`run_detail.html.jinja2`)

Three conditional modifications, all gated on `{% if preview %}`:

1. **Banner** — a yellow "Preview Mode — fake data" bar rendered just below the `<nav>`, only when `preview=True`

2. **Newsletter iframe** — replaced with a grey placeholder panel ("Newsletter preview not available") when `preview=True`; the real iframe renders as-is otherwise

3. **Filtered/failed JS fetches** — when `preview=True`, skip the `DOMContentLoaded` fetch calls and instead call `renderFiltered([])` and `renderFailed([])` directly so the sidebar sections show their empty-state messages without making requests that would 404

## Fake Data Shape

```python
PREVIEW_ARTICLES = {
    "Anthropic Blog":   [{"title": "...", "url": "#", "rank_score": 9.8}, ...],
    "OpenAI Blog":      [...],
    "ArXiv":            [...],
    "MIT Tech Review":  [...],
    "Hacker News":      [...],
}
```

Scores are spread across the full 4.0–9.8 range to exercise the score display at realistic values. URLs are `"#"` (no external links needed for a preview).

## What Is Not Changing

- No new template file
- No DB schema changes
- No change to the real `GET /runs/{run_id}` route
- The preview page is not linked from the dashboard nav (dev-only URL)
