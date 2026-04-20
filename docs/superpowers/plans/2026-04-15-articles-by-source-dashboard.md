# Articles-by-Source Dashboard Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collapsible "Articles by Source" panel to the dashboard showing every fetched article grouped by source with clickable links.

**Architecture:** Add a `run_articles` SQLite table, bulk-insert articles immediately after fetch (before processing), query and group them in `GET /`, then render a `<details>` panel in the dashboard template.

**Tech Stack:** SQLite (via `sqlite3`), FastAPI, Jinja2, HTML/CSS (`<details>`/`<summary>`)

---

## File Map

| File | Change |
|------|--------|
| `src/db.py` | Add `_CREATE_RUN_ARTICLES` constant and `conn.execute()` call in `get_connection()` |
| `src/main.py` | Bulk-insert fetched articles into `run_articles` after `fetch_articles()` returns |
| `src/server.py` | Query `run_articles` for latest successful run, group by publication, pass `sources` to template |
| `templates/dashboard.html.jinja2` | Add `<details>` panel with CSS, source groups, and article links |

---

### Task 1: Add `run_articles` table to `src/db.py`

**Files:**
- Modify: `src/db.py`

- [ ] **Step 1: Add the table DDL constant and execute it in `get_connection()`**

In `src/db.py`, after `_CREATE_RUNS`, add:

```python
_CREATE_RUN_ARTICLES = """
CREATE TABLE IF NOT EXISTS run_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    publication  TEXT,
    published_at TEXT
);
"""
```

And inside `get_connection()`, after `conn.execute(_CREATE_RUNS)`:

```python
conn.execute(_CREATE_RUN_ARTICLES)
```

- [ ] **Step 2: Verify the server still starts**

```bash
poetry run python -c "from src.db import get_connection; conn = get_connection(); print(conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()); conn.close()"
```

Expected: output includes `run_articles` in the list.

- [ ] **Step 3: Commit**

```bash
git add src/db.py
git commit -m "feat: add run_articles table to db schema"
```

---

### Task 2: Bulk-insert articles after fetch in `src/main.py`

**Files:**
- Modify: `src/main.py:51-57`

- [ ] **Step 1: Insert the bulk-insert block after `fetch_articles()`**

In `run_pipeline()`, replace:

```python
        log.info("[%s] Fetched %d articles", run_id, len(articles))

        provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
```

With:

```python
        log.info("[%s] Fetched %d articles", run_id, len(articles))

        conn = get_connection()
        conn.executemany(
            "INSERT INTO run_articles (run_id, title, url, publication, published_at) VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, a["title"], a["url"], a["publication"], str(a.get("published_at", "")))
                for a in articles
            ],
        )
        conn.commit()
        conn.close()

        provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
```

- [ ] **Step 2: Run the dry-run to verify no import errors**

```bash
poetry run python -m src.main --help
```

Expected: prints argparse help without errors.

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: persist fetched articles to run_articles table"
```

---

### Task 3: Query and group articles in `src/server.py`

**Files:**
- Modify: `src/server.py:25-38`

- [ ] **Step 1: Update the `dashboard` endpoint**

Replace the entire `dashboard` function:

```python
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Serve the run-history dashboard."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, started_at, completed_at, status, article_count, error "
        "FROM runs ORDER BY started_at DESC"
    ).fetchall()
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
    conn.close()

    runs = [dict(row) for row in rows]

    sources: dict[str, list[dict]] = {}
    for row in source_rows:
        pub = row["publication"] or "Unknown"
        sources.setdefault(pub, []).append({"title": row["title"], "url": row["url"]})

    return templates.TemplateResponse(
        request,
        "dashboard.html.jinja2",
        {"runs": runs, "running": _is_running, "sources": sources},
    )
```

- [ ] **Step 2: Verify the server loads**

```bash
poetry run python -c "from src.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/server.py
git commit -m "feat: query and pass sources to dashboard template"
```

---

### Task 4: Add collapsible panel to `templates/dashboard.html.jinja2`

**Files:**
- Modify: `templates/dashboard.html.jinja2`

- [ ] **Step 1: Add CSS for the panel** (inside `<style>`, after `.article-count` block at line 215):

```css
    /* Sources panel */
    .sources-section {
      margin-top: 32px;
    }
    .sources-section > summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 18px;
      font-weight: 700;
      color: #1a1a2e;
      padding-bottom: 8px;
      border-bottom: 2px solid #5b5bd6;
      margin-bottom: 20px;
      user-select: none;
    }
    .sources-section > summary::-webkit-details-marker { display: none; }
    .sources-section > summary::before {
      content: "▶";
      font-size: 12px;
      color: #5b5bd6;
      transition: transform 0.2s;
    }
    .sources-section[open] > summary::before {
      transform: rotate(90deg);
    }
    .source-run-label {
      font-size: 13px;
      font-weight: 400;
      color: #888;
    }
    .source-group {
      margin-bottom: 20px;
    }
    .source-name {
      font-size: 15px;
      font-weight: 700;
      color: #1a1a2e;
      margin: 0 0 8px 0;
    }
    .source-count {
      font-size: 13px;
      font-weight: 400;
      color: #5b5bd6;
    }
    .article-list {
      margin: 0;
      padding-left: 20px;
      list-style: disc;
    }
    .article-list li {
      margin-bottom: 4px;
      font-size: 14px;
    }
    .article-list a {
      color: #5b5bd6;
      text-decoration: none;
    }
    .article-list a:hover {
      text-decoration: underline;
    }
```

- [ ] **Step 2: Add the panel HTML** (inside `.body-card`, after the closing `{% endif %}` of the runs table at line 336, before the closing `</div>` of `.body-card`):

```html
      <!-- Articles by Source -->
      <details class="sources-section">
        <summary>
          Articles by Source
          <span class="source-run-label">(most recent successful run)</span>
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

- [ ] **Step 3: Verify template renders**

```bash
poetry run python -c "
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
t = env.get_template('dashboard.html.jinja2')
out = t.render(runs=[], running=False, sources={})
print('OK' if 'sources-section' in out else 'FAIL')
"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard.html.jinja2
git commit -m "feat: add collapsible Articles by Source panel to dashboard"
```
