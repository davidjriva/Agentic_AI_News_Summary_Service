# LangChain Scraper Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two crash bugs and four code quality issues in the LangChain blog scraper introduced in commit `8ca269e`.

**Architecture:** All changes are confined to `src/fetcher.py` (logic fixes) and `tests/test_fetcher.py` (new tests). No new files. Tasks are ordered: critical bugs first (TDD), then cleanup.

**Tech Stack:** Python 3.11, pytest, unittest.mock, BeautifulSoup4

---

## File Map

| File | Changes |
|------|---------|
| `src/fetcher.py` | Fix crash bugs; remove stale comment; move import; use `_domain()`; remove unused `now` param |
| `tests/test_fetcher.py` | Add 5 new tests for `_process_langchain` |

---

### Task 1: Fix critical crash bugs — unbound `link` and null parent walk

**Files:**
- Modify: `src/fetcher.py:259–269`
- Test: `tests/test_fetcher.py`

The current code has two bugs in the DOM walk loop (lines 261–268):

1. `link` is only assigned inside `if link: break`, so if no link is found after 6 hops, `if not link:` at line 268 raises `UnboundLocalError`.
2. `card.parent` called on the BeautifulSoup document root returns `None`; the next iteration then calls `None.find(...)`, raising `AttributeError`.

Both bugs are triggered by the same condition: an `<h2>` with no `/blog/` ancestor within 6 levels.

- [ ] **Step 1: Write the failing test**

Add this test to the bottom of `tests/test_fetcher.py`:

```python
# ---------------------------------------------------------------------------
# _process_langchain — crash safety: h2 with no /blog/ ancestor link
# ---------------------------------------------------------------------------

def test_langchain_no_blog_link_returns_empty():
    """_process_langchain must return [] when an h2 has no /blog/ ancestor link."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    # Shallow HTML: h2 is a direct child of body — only 2 parent levels before document root
    html = """
    <html><body>
      <h2 class="t-heading-6-rg">Orphan Title</h2>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime.now(timezone.utc)
    cutoff = now  # nothing passes recency anyway

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_fetcher.py::test_langchain_no_blog_link_returns_empty -v
```

Expected: FAIL with `UnboundLocalError: cannot access local variable 'link' before assignment`

- [ ] **Step 3: Apply the fix**

In `src/fetcher.py`, replace lines 261–269 (the loop + guard):

```python
        link = None
        card = h2.parent
        for _ in range(6):
            if card is None:
                break
            link = card.find("a", href=lambda h: h and h.startswith("/blog/"))
            if link:
                break
            card = card.parent

        if not link:
            continue
```

The full updated `_process_langchain` loop section (lines 259–270) becomes:

```python
    for h2 in soup.find_all("h2", class_="t-heading-6-rg"):
        title = h2.get_text(strip=True)
        link = None
        card = h2.parent
        for _ in range(6):
            if card is None:
                break
            link = card.find("a", href=lambda h: h and h.startswith("/blog/"))
            if link:
                break
            card = card.parent

        if not link:
            continue
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_fetcher.py::test_langchain_no_blog_link_returns_empty -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/fetcher.py tests/test_fetcher.py
git commit -m "fix: guard against null parent and unbound link in _process_langchain DOM walk"
```

---

### Task 2: Add happy-path test for `_process_langchain`

**Files:**
- Test: `tests/test_fetcher.py`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `tests/test_fetcher.py`:

```python
def test_langchain_happy_path_returns_article():
    """_process_langchain returns a correctly shaped article for a recent post."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock, patch

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Agent Engineering Deep Dive</h2>
        <div class="date-color">April 17, 2026</div>
        <div class="text-c-blue-light-500">Jane Smith</div>
        <a href="/blog/agent-engineering-deep-dive" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert len(result) == 1
    article = result[0]
    assert article["title"] == "Agent Engineering Deep Dive"
    assert article["url"] == "https://www.langchain.com/blog/agent-engineering-deep-dive"
    assert article["author"] == "Jane Smith"
    assert article["publication"] == "www.langchain.com"
    assert isinstance(article["published_at"], datetime)
    assert article["published_at"].year == 2026
    assert article["published_at"].month == 4
    assert article["published_at"].day == 17
```

- [ ] **Step 2: Run test to verify it passes**

```bash
poetry run pytest tests/test_fetcher.py::test_langchain_happy_path_returns_article -v
```

Expected: PASS (no code change needed — this is verifying existing behavior)

- [ ] **Step 3: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_fetcher.py
git commit -m "test: add happy-path test for _process_langchain"
```

---

### Task 3: Add recency and dedup tests for `_process_langchain`

**Files:**
- Test: `tests/test_fetcher.py`

- [ ] **Step 1: Write the failing tests**

Add both tests to the bottom of `tests/test_fetcher.py`:

```python
def test_langchain_old_article_filtered():
    """_process_langchain must exclude articles published before the cutoff."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock, patch

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Stale Post</h2>
        <div class="date-color">January 1, 2025</div>
        <div class="text-c-blue-light-500">Old Author</div>
        <a href="/blog/stale-post" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []


def test_langchain_seen_url_skipped():
    """_process_langchain must skip URLs already present in seen_urls."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock, patch

    url = "https://www.langchain.com/blog/already-seen"

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Already Seen</h2>
        <div class="date-color">April 17, 2026</div>
        <div class="text-c-blue-light-500">Author</div>
        <a href="/blog/already-seen" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain(
            "https://www.langchain.com/blog", now, cutoff, {url}
        )

    assert result == []
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
poetry run pytest tests/test_fetcher.py::test_langchain_old_article_filtered tests/test_fetcher.py::test_langchain_seen_url_skipped -v
```

Expected: both PASS (verifying existing behavior)

- [ ] **Step 3: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_fetcher.py
git commit -m "test: add recency and dedup tests for _process_langchain"
```

---

### Task 4: Add graceful-failure tests for `_process_langchain`

**Files:**
- Test: `tests/test_fetcher.py`

- [ ] **Step 1: Write the failing tests**

Add both tests to the bottom of `tests/test_fetcher.py`:

```python
def test_langchain_http_error_returns_empty():
    """_process_langchain must return [] when the HTTP request fails."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    import requests as req_lib

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", side_effect=req_lib.RequestException("timeout")):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []


def test_langchain_missing_html_structure_returns_empty():
    """_process_langchain must return [] when the page has no expected h2 elements."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock, patch

    html = "<html><body><p>Nothing here.</p></body></html>"

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
poetry run pytest tests/test_fetcher.py::test_langchain_http_error_returns_empty tests/test_fetcher.py::test_langchain_missing_html_structure_returns_empty -v
```

Expected: both PASS

- [ ] **Step 3: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_fetcher.py
git commit -m "test: add graceful-failure tests for _process_langchain"
```

---

### Task 5: Code cleanup — stale comment, top-level import, use `_domain()`

**Files:**
- Modify: `src/fetcher.py`

Three independent cosmetic fixes with no logic change:
1. Remove the stale `# soft import — not in base requirements` comment and move `from bs4 import BeautifulSoup` to the top-level imports.
2. Replace hardcoded `"www.langchain.com"` with `_domain(blog_url)`.

- [ ] **Step 1: Move BeautifulSoup import to the top of the file**

In `src/fetcher.py`, add `from bs4 import BeautifulSoup` to the top-level imports block (after `import feedparser`):

```python
import feedparser
import requests
from bs4 import BeautifulSoup
```

Then remove these two lines from inside `_process_langchain` (currently around line 249):

```python
        from bs4 import BeautifulSoup  # soft import — not in base requirements

```

The `try` block in `_process_langchain` should now start directly with:

```python
    try:
        resp = requests.get(blog_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return articles
```

- [ ] **Step 2: Replace hardcoded publication string with `_domain()`**

In `_process_langchain`, find the article dict construction and replace the hardcoded string:

Old:
```python
            "publication": "www.langchain.com",
```

New:
```python
            "publication": _domain(blog_url),
```

- [ ] **Step 3: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass (no behavior change)

- [ ] **Step 4: Commit**

```bash
git add src/fetcher.py
git commit -m "refactor: move BeautifulSoup import to top level; use _domain() in _process_langchain"
```

---

### Task 6: Remove unused `now` parameter from `_process_hn` and `_process_langchain`

**Files:**
- Modify: `src/fetcher.py:114–124` (`_dispatch_feed`), `src/fetcher.py:194–232` (`_process_hn`), `src/fetcher.py:235–303` (`_process_langchain`)

Neither `_process_hn` nor `_process_langchain` uses the `now` parameter it receives. `_dispatch_feed` passes `now` to both. Remove `now` from all three call sites.

`_process_rss` and `_fetch_parallel` are not affected.

- [ ] **Step 1: Update `_dispatch_feed`**

Replace the entire `_dispatch_feed` function with:

```python
def _dispatch_feed(
    feed_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    if feed_url == HN_ALGOLIA_URL:
        return _process_hn(feed_url, cutoff, seen_urls)
    if feed_url == LANGCHAIN_BLOG_URL:
        return _process_langchain(feed_url, cutoff, seen_urls)
    return _process_rss(feed_url, cutoff, seen_urls)
```

(`now` stays in `_dispatch_feed`'s signature because `_fetch_parallel` passes it; it just no longer forwards it.)

- [ ] **Step 2: Update `_process_hn` signature**

Replace:
```python
def _process_hn(
    hn_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
```

With:
```python
def _process_hn(
    hn_url: str,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
```

- [ ] **Step 3: Update `_process_langchain` signature**

Replace:
```python
def _process_langchain(
    blog_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
```

With:
```python
def _process_langchain(
    blog_url: str,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
```

- [ ] **Step 4: Update all test calls to `_process_langchain` (remove the `now` argument)**

In `tests/test_fetcher.py`, every call to `_process_langchain(...)` currently passes `now` as the second positional argument. Update each one to remove it.

Old pattern (appears in Tasks 1–4 tests above):
```python
result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())
```

New pattern:
```python
result = _process_langchain("https://www.langchain.com/blog", cutoff, set())
```

There are 5 call sites total in the tests added by Tasks 1–4:
- `test_langchain_no_blog_link_returns_empty`
- `test_langchain_happy_path_returns_article`
- `test_langchain_old_article_filtered`
- `test_langchain_seen_url_skipped`
- `test_langchain_http_error_returns_empty`
- `test_langchain_missing_html_structure_returns_empty`

Update all 6.

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/fetcher.py tests/test_fetcher.py
git commit -m "refactor: remove unused 'now' parameter from _process_hn and _process_langchain"
```
