# Articles-by-Source Dashboard Panel — Design Spec

**Date:** 2026-04-15
**Status:** Approved

## Goal

Add a collapsible "Articles by Source" section to the existing dashboard at `localhost:8000`. After each pipeline run, the user can expand this panel to see every fetched article grouped by source, with a count per source and a clickable original link for each article. This supports monitoring of source health (underutilization or fetch failures).

---

## Architecture

Four files are touched; no new files are created.

### 1. `src/db.py` — New `run_articles` table

```sql
CREATE TABLE IF NOT EXISTS run_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    publication  TEXT,
    published_at TEXT
);
```

Added as a `CREATE TABLE IF NOT EXISTS` string and executed inside `get_connection()`, alongside the existing `seen_articles` and `runs` table creation. Auto-creates on first connection after deploy — no migration script required.

### 2. `src/main.py` — Persist articles after fetch

Immediately after `fetch_articles()` returns and before `process_articles()` runs, bulk-insert all fetched articles into `run_articles`:

```python
conn.executemany(
    "INSERT INTO run_articles (run_id, title, url, publication, published_at) VALUES (?, ?, ?, ?, ?)",
    [
        (run_id, a["title"], a["url"], a["publication"], str(a.get("published_at", "")))
        for a in articles
    ],
)
conn.commit()
conn.close()
```

Inserted unconditionally — if a later stage fails, fetch data is still visible in the dashboard for debugging purposes.

### 3. `src/server.py` — Query and group articles for dashboard

In the `GET /` endpoint, after loading run history, execute one additional query:

```python
source_rows = conn.execute(
    """
    SELECT publication, title, url
    FROM run_articles
    WHERE run_id = (
        SELECT id FROM runs WHERE status = 'success' ORDER BY started_at DESC LIMIT 1
    )
    ORDER BY publication, title
    """
).fetchall()
```

Group results in Python into `dict[str, list[dict]]` keyed by `publication`. Pass as `sources` to the template. If no successful run exists, `sources` is an empty dict.

### 4. `templates/dashboard.html.jinja2` — Collapsible panel

A `<details>`/`<summary>` block appended inside `.body-card`, after the run history table:

```html
<details class="sources-section">
  <summary class="section-heading">
    Articles by Source
    <span class="source-run-label">(most recent run)</span>
  </summary>
  {% if sources %}
    {% for publication, articles in sources.items() %}
    <div class="source-group">
      <h3 class="source-name">
        {{ publication }}
        <span class="source-count">({{ articles|length }})</span>
      </h3>
      <ul class="article-list">
        {% for article in articles %}
        <li>
          <a href="{{ article.url }}" target="_blank" rel="noopener">{{ article.title }}</a>
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endfor %}
  {% else %}
    <div class="empty-state">No article data yet — run the pipeline first.</div>
  {% endif %}
</details>
```

Styled to match the existing dark-header, purple-accent design. Collapsed by default.

---

## Data Flow

```
run_pipeline()
  └── fetch_articles()          → list[dict]
        └── INSERT run_articles (run_id, title, url, publication, published_at)
  └── process_articles()
  └── rank_articles()
  └── render_newsletter()
  └── UPDATE runs (status, html, article_count)

GET /
  └── SELECT runs (history)
  └── SELECT run_articles WHERE run_id = latest successful run
        └── group by publication in Python
  └── render dashboard.html.jinja2 { runs, running, sources }
```

---

## Error Handling

- If `fetch_articles()` returns an empty list, the `executemany` is a no-op.
- If the pipeline errors after fetch, the `run_articles` rows are still committed — the dashboard shows article data even for failed runs (keyed to the most recent *successful* run, so a failed run's data won't surface until it succeeds).
- If no successful run exists yet, `sources` is `{}` and the template shows an empty-state message.

---

## Testing

Existing tests mock at external boundaries (`feedparser.parse`, `requests.post`). No new test files are needed; the `run_articles` insert is a side effect of `run_pipeline()` and is covered by integration-style assertions in the existing pipeline test if desired. The DB auto-creation path is already tested via `test_db.py` patterns.

---

## Out of Scope

- Cross-run source trend views (e.g. "source X fetched 0 articles for 3 runs in a row")
- Filtering or searching articles within the panel
- Showing articles for non-latest runs
