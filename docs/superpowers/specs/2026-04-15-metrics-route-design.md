# /metrics Route — Design Spec

**Date:** 2026-04-15
**Status:** Approved

## Goal

Add a `/metrics` page to the FastAPI dashboard that visualizes lifetime pipeline and content stats: total emails delivered, subscriber count, articles seen, run health, source distribution, and article volume trends. A two-tab nav (`Run History` | `Metrics`) links the existing dashboard to the new page.

---

## Architecture

Four files touched, one new file created.

| File | Change |
|---|---|
| `src/server.py` | Add `GET /metrics` route |
| `templates/metrics.html.jinja2` | New template with Chart.js |
| `templates/dashboard.html.jinja2` | Add two-tab nav strip to header |
| `templates/run_detail.html.jinja2` | Add two-tab nav strip to header |

No DB schema changes — all required data exists in `runs`, `run_articles`, and `seen_articles`.

---

## Route: `GET /metrics`

Handler in `src/server.py`. Opens one DB connection, runs all queries, closes it, then passes data to the template.

### Queries

| Variable | Query / Source |
|---|---|
| `subscribers` | `len(config.RECIPIENTS)` |
| `successful_runs` | `SELECT COUNT(*) FROM runs WHERE status='success'` |
| `total_runs` | `SELECT COUNT(*) FROM runs` |
| `emails_delivered` | `successful_runs × subscribers` |
| `articles_seen` | `SELECT COUNT(*) FROM seen_articles` |
| `articles_by_source` | `SELECT publication, COUNT(*) FROM run_articles GROUP BY publication ORDER BY 2 DESC` |
| `status_counts` | `SELECT status, COUNT(*) FROM runs GROUP BY status` |
| `articles_per_run` | `SELECT started_at, article_count FROM runs WHERE status='success' ORDER BY started_at ASC LIMIT 20` |

### Template variables passed

All chart data is JSON-serialized by the handler (via `json.dumps`) and passed as strings to the template to avoid Jinja2 escaping issues with Chart.js inline data.

```python
return templates.TemplateResponse(request, "metrics.html.jinja2", {
    "subscribers": subscribers,
    "emails_delivered": emails_delivered,
    "total_runs": total_runs,
    "articles_seen": articles_seen,
    "sources_labels_json": json.dumps([row["publication"] for row in articles_by_source]),
    "sources_data_json": json.dumps([row["count"] for row in articles_by_source]),
    "run_status_labels_json": json.dumps(list(status_counts.keys())),
    "run_status_data_json": json.dumps(list(status_counts.values())),
    "run_dates_json": json.dumps([row["started_at"][:10] for row in articles_per_run]),
    "run_counts_json": json.dumps([row["article_count"] for row in articles_per_run]),
})
```

---

## Template: `metrics.html.jinja2`

Matches the existing dark-header / purple-accent / white-card design system.

### Structure

```
Header (dark, shared)
  Nav tabs: [Run History] [Metrics ←active]
Body card
  Section: Lifetime Stats
    4-column stat grid:
      Emails Delivered | Subscribers | Total Runs | Articles Seen
  Section: Content & Pipeline
    2-column chart row:
      Horizontal bar — Articles by Source (lifetime, run_articles)
      Doughnut — Run Success Rate (success / error / running)
  Section: Articles per Run
    Full-width line chart — article_count across last 20 successful runs
Footer (dark, shared)
```

### Charts

Chart.js loaded via CDN (`chart.js@4` from cdnjs). Data baked in via inline `<script>` tags:

```html
<script>
  const SOURCES_LABELS = {{ sources_labels_json | safe }};
  const SOURCES_DATA   = {{ sources_data_json | safe }};
  // ... etc
</script>
```

- **Horizontal bar** (`indexAxis: 'y'`): publication names on y-axis, count on x-axis. Purple fill (`#5b5bd6`).
- **Doughnut**: success/error/running slices. Colors: green `#1a7a4a`, red `#9b2020`, yellow `#856404`.
- **Line**: x = run date (truncated to `YYYY-MM-DD`), y = `article_count`. Purple line, light purple fill below.

---

## Nav Tabs

A tab strip added to both `dashboard.html.jinja2` and `run_detail.html.jinja2` between the header `<div>` and the body card. Active tab highlighted with purple bottom border; inactive tab is muted.

```html
<div class="nav-tabs">
  <a class="nav-tab {% if active == 'dashboard' %}active{% endif %}" href="/">Run History</a>
  <a class="nav-tab {% if active == 'metrics' %}active{% endif %}" href="/metrics">Metrics</a>
</div>
```

Each template sets an `active` context variable; `run_detail.html.jinja2` passes neither (no active tab highlighted), since it's a drill-down view not a top-level tab.

---

## Data Flow

```
GET /metrics
  └── open DB connection
  └── query: seen_articles count
  └── query: runs (total count, success count, status breakdown, per-run counts)
  └── query: run_articles (group by publication)
  └── read config.RECIPIENTS
  └── close DB connection
  └── render metrics.html.jinja2 with all vars
```

---

## Error Handling

- If `RECIPIENTS` is empty, `subscribers = 0` and `emails_delivered = 0`. Stat cards show `0`.
- If no runs exist yet, all chart datasets are empty arrays — Chart.js renders empty axes gracefully.
- If `run_articles` is empty, the source bar chart renders with no bars (empty state handled by Chart.js).

---

## Testing

One new test class `TestMetricsRoute` in `tests/test_server.py`:

- `test_metrics_renders` — GET `/metrics` returns 200 with expected stat values in body
- `test_metrics_empty_state` — GET `/metrics` with empty DB returns 200 (no crash on empty charts)

Follows the existing pattern: `TestClient(app)` with `temp_db` fixture, no live DB.

---

## Out of Scope

- Per-source trend over time (e.g. "source X sent 0 articles for 3 consecutive runs")
- Filtering charts by date range
- Email open/click tracking
- Real-time refresh of metrics
