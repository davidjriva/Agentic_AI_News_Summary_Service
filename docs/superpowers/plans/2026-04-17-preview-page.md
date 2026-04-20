# Preview Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/preview` route that renders the run detail UI with hardcoded fake AI news articles so the rank_score display can be verified without running the pipeline.

**Architecture:** A new `GET /preview` handler in `src/server.py` passes `preview=True` and a `_PREVIEW_ARTICLES` constant to the existing `run_detail.html.jinja2` template. Three conditional blocks in the template gate on `preview`: a yellow banner, an iframe replacement, and a JS fetch bypass.

**Tech Stack:** FastAPI, Jinja2, Python 3.x, pytest + FastAPI TestClient

---

## Files

- Modify: `src/server.py` — add `_PREVIEW_ARTICLES` constant and `GET /preview` route
- Modify: `templates/run_detail.html.jinja2` — add banner CSS, banner HTML, iframe conditional, JS fetch conditional
- Modify: `tests/test_server.py` — add `TestPreviewRoute` test class

---

### Task 1: Add `_PREVIEW_ARTICLES` constant and `/preview` route to `server.py`

**Files:**
- Modify: `src/server.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_server.py` (no `temp_db` fixture needed — `/preview` makes no DB calls):

```python
# ---------------------------------------------------------------------------
# GET /preview
# ---------------------------------------------------------------------------

class TestPreviewRoute:
    @pytest.fixture()
    def preview_client(self):
        from src.server import app
        return TestClient(app)

    def test_preview_returns_200(self, preview_client):
        response = preview_client.get("/preview")
        assert response.status_code == 200

    def test_preview_contains_banner(self, preview_client):
        response = preview_client.get("/preview")
        assert "Preview Mode" in response.text

    def test_preview_contains_articles_by_source(self, preview_client):
        response = preview_client.get("/preview")
        assert "Articles by Source" in response.text

    def test_preview_shows_fake_article_titles(self, preview_client):
        from src.server import _PREVIEW_ARTICLES
        response = preview_client.get("/preview")
        for articles in _PREVIEW_ARTICLES.values():
            for article in articles:
                assert article["title"] in response.text

    def test_preview_has_no_newsletter_iframe_src(self, preview_client):
        response = preview_client.get("/preview")
        assert "/runs/preview/newsletter" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_server.py::TestPreviewRoute -v
```

Expected: FAIL — `ImportError` or 404 (route does not exist yet)

- [ ] **Step 3: Add `_PREVIEW_ARTICLES` constant to `src/server.py`**

Insert this block after the `_TEMPLATES_DIR` and `templates` lines, before the `app = FastAPI(...)` line:

```python
_PREVIEW_ARTICLES: dict[str, list[dict]] = {
    "Anthropic Blog": [
        {"title": "Claude 4 Achieves State-of-the-Art on Long-Context Reasoning", "url": "#", "rank_score": 9.8},
        {"title": "Introducing Constitutional AI v2: Safer by Default", "url": "#", "rank_score": 8.4},
    ],
    "OpenAI Blog": [
        {"title": "GPT-5 Technical Report: Multimodal Capabilities and Alignment", "url": "#", "rank_score": 9.2},
        {"title": "DALL-E 4 Released with Real-Time Video Generation", "url": "#", "rank_score": 7.6},
    ],
    "ArXiv": [
        {"title": "Scaling Laws for Mixture-of-Experts Language Models", "url": "#", "rank_score": 8.8},
        {"title": "Self-Play Fine-Tuning Converts Weak to Strong Language Models", "url": "#", "rank_score": 7.2},
        {"title": "RoPE Scaling Methods for Long-Context LLMs: A Survey", "url": "#", "rank_score": 5.8},
    ],
    "MIT Technology Review": [
        {"title": "AI Regulation in 2026: What the EU Act Means for Developers", "url": "#", "rank_score": 6.4},
        {"title": "The Hidden Carbon Cost of Training Large Language Models", "url": "#", "rank_score": 5.2},
    ],
    "Hacker News": [
        {"title": "Ask HN: What's your current local LLM setup in 2026?", "url": "#", "rank_score": 4.8},
        {"title": "Llama 4 70B runs at 120 tok/s on a single RTX 5090", "url": "#", "rank_score": 6.0},
        {"title": "Show HN: Open-source tool for LLM prompt versioning", "url": "#", "rank_score": 4.0},
    ],
}
```

- [ ] **Step 4: Add `GET /preview` route to `src/server.py`**

Insert after the `GET /` dashboard route (after its closing brace, before `POST /run`):

```python
@app.get("/preview", response_class=HTMLResponse)
def preview(request: Request) -> HTMLResponse:
    """Render the run detail page with fake articles for UI development."""
    return templates.TemplateResponse(
        request,
        "run_detail.html.jinja2",
        {
            "run_id": "preview",
            "started_at": "Preview Mode",
            "sources": _PREVIEW_ARTICLES,
            "preview": True,
        },
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/test_server.py::TestPreviewRoute -v
```

Expected: all 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/server.py tests/test_server.py
git commit -m "feat: add /preview route with fake AI news articles"
```

---

### Task 2: Update `run_detail.html.jinja2` with preview-mode UI

**Files:**
- Modify: `templates/run_detail.html.jinja2`

- [ ] **Step 1: Add banner and placeholder CSS**

Find the closing `</style>` tag (line ~346) and insert before it:

```css
    /* ── PREVIEW BANNER ─────────────────────────────── */
    .preview-banner {
      background: #FFF3CD;
      border-bottom: 1px solid #FFECB5;
      color: #856404;
      font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
      font-size: 12px;
      font-weight: 600;
      text-align: center;
      padding: 6px 20px;
      letter-spacing: 0.3px;
    }

    .newsletter-placeholder {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      background: var(--bg-section);
      color: var(--gray-mid);
      font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
      font-size: 13px;
      font-style: italic;
    }
```

- [ ] **Step 2: Add banner HTML after `</nav>`**

Find:
```html
    <!-- ── SPLIT LAYOUT ─────────────────────────────── -->
```

Insert immediately before it:
```html
    {% if preview %}
    <div class="preview-banner">Preview Mode &mdash; fake data</div>
    {% endif %}

```

- [ ] **Step 3: Replace the newsletter iframe with a conditional**

Find:
```html
      <!-- Newsletter iframe -->
      <div class="newsletter-pane">
        <iframe src="/runs/{{ run_id }}/newsletter" title="Newsletter Edition"></iframe>
      </div>
```

Replace with:
```html
      <!-- Newsletter iframe -->
      <div class="newsletter-pane">
        {% if preview %}
        <div class="newsletter-placeholder">Newsletter preview not available</div>
        {% else %}
        <iframe src="/runs/{{ run_id }}/newsletter" title="Newsletter Edition"></iframe>
        {% endif %}
      </div>
```

- [ ] **Step 4: Replace the JS `DOMContentLoaded` fetch block with a conditional**

Find the `document.addEventListener('DOMContentLoaded', ...` block at the bottom of the `<script>` tag:

```javascript
    document.addEventListener('DOMContentLoaded', () => {
      fetch(`/runs/${RUN_ID}/filtered`)
        .then(r => r.json())
        .then(data => renderFiltered(data.filtered))
        .catch(() => renderFiltered([]));

      fetch(`/runs/${RUN_ID}/failed`)
        .then(r => r.json())
        .then(data => renderFailed(data.failed))
        .catch(() => renderFailed([]));
    });
```

Replace with:
```javascript
    {% if preview %}
    renderFiltered([]);
    renderFailed([]);
    {% else %}
    document.addEventListener('DOMContentLoaded', () => {
      fetch(`/runs/${RUN_ID}/filtered`)
        .then(r => r.json())
        .then(data => renderFiltered(data.filtered))
        .catch(() => renderFiltered([]));

      fetch(`/runs/${RUN_ID}/failed`)
        .then(r => r.json())
        .then(data => renderFailed(data.failed))
        .catch(() => renderFailed([]));
    });
    {% endif %}
```

- [ ] **Step 5: Verify tests still pass**

```bash
poetry run pytest tests/test_server.py -v
```

Expected: all tests PASS (no regressions)

- [ ] **Step 6: Manually verify in browser**

Start the server and open `http://localhost:8000/preview`:

```bash
make serve
```

Check:
- Yellow "Preview Mode — fake data" banner visible below nav
- Grey "Newsletter preview not available" panel where iframe would be
- Sidebar shows all 5 publications with article titles and rank scores
- "Dropped" and "Failed" sections show empty-state messages (not "Loading…")

- [ ] **Step 7: Commit**

```bash
git add templates/run_detail.html.jinja2
git commit -m "feat: add preview mode banner, iframe placeholder, and fetch bypass to run detail template"
```
