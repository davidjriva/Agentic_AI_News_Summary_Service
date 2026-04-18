# Article Score Cache & Dedup Scope Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache LLM-computed article scores in SQLite to skip re-computation on repeat URLs, and narrow seen-article deduplication to only the top-10 newsletter articles.

**Architecture:** A new `article_scores` table (keyed by URL, 3-day TTL) is read at the start of `process_articles()` and written on each LLM success. `fetch_articles()` stops writing to `seen_articles`; `main.py` writes the top-N ranked URLs to `seen_articles` after ranking. `LOOKBACK_HOURS` expands from 24 to 72, and `SCORE_CACHE_TTL_DAYS` is derived from it.

**Tech Stack:** Python 3.11+, SQLite (via `sqlite3` stdlib), `pytest`, `unittest.mock`

---

## File Map

| File | Change |
|---|---|
| `src/config.py` | `LOOKBACK_HOURS: 24 → 72`; add `SCORE_CACHE_TTL_DAYS` |
| `src/db.py` | Add `article_scores` DDL + `get_connection()` call |
| `src/fetcher.py` | Remove `seen_articles` insert; add `article_scores` prune; simplify return types |
| `src/processor.py` | Add `_load_score_cache()`, `_write_score_cache()`, cache lookup in `_process_one()` |
| `src/main.py` | Insert top-N URLs into `seen_articles` after `rank_articles()` |
| `tests/test_db.py` | Add `article_scores` table existence test |
| `tests/test_fetcher.py` | Update `tmp_db` fixture; replace insert test with no-insert test; add prune test |
| `tests/test_processor.py` | Add `TestScoreCache` class |
| `tests/test_main.py` | Add test that top-N URLs are written to `seen_articles` |

---

## Task 1: Expand lookback window and add TTL constant in config.py

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Edit `src/config.py`**

Replace:
```python
LOOKBACK_HOURS: int = 24
```
With:
```python
LOOKBACK_HOURS: int = 72
SCORE_CACHE_TTL_DAYS: int = LOOKBACK_HOURS // 24  # 3 — aligned with lookback window
```

- [ ] **Step 2: Run existing tests to confirm nothing breaks**

```bash
poetry run pytest tests/ -v
```
Expected: all tests pass (LOOKBACK_HOURS is used in fetcher; existing tests mock it or use small values)

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat: expand LOOKBACK_HOURS to 72h and derive SCORE_CACHE_TTL_DAYS"
```

---

## Task 2: Add `article_scores` table to db.py

**Files:**
- Modify: `src/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write the failing test in `tests/test_db.py`**

Add after the existing tests:
```python
def test_article_scores_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='article_scores'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_article_scores_has_expected_columns(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(article_scores)")
    cols = {row[1] for row in cursor.fetchall()}
    expected = {
        "url", "impact_score", "authenticity_score", "relevance_score",
        "impact_reason", "authenticity_reason", "relevance_reason", "cached_at",
    }
    assert expected == cols
    conn.close()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
poetry run pytest tests/test_db.py::test_article_scores_table_exists tests/test_db.py::test_article_scores_has_expected_columns -v
```
Expected: FAIL — table does not exist yet

- [ ] **Step 3: Add DDL constant and create call in `src/db.py`**

After `_CREATE_FILTERED_ARTICLES`, add:
```python
_CREATE_ARTICLE_SCORES = """
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
"""
```

In `get_connection()`, after the `conn.execute(_CREATE_FILTERED_ARTICLES)` call, add:
```python
    conn.execute(_CREATE_ARTICLE_SCORES)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
poetry run pytest tests/test_db.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: add article_scores table to SQLite schema"
```

---

## Task 3: Update `fetcher.py` — remove seen_articles insert, add article_scores prune

**Files:**
- Modify: `src/fetcher.py`
- Modify: `tests/test_fetcher.py`

**Context:** `_process_rss` and `_process_hn` currently return `tuple[list[dict], list[tuple[str, str]]]`. We simplify them to return `list[dict]`. The batch insert of new URLs into `seen_articles` is removed entirely. `fetch_articles()` now prunes `article_scores` alongside `seen_articles`.

- [ ] **Step 1: Update `tmp_db` fixture in `tests/test_fetcher.py` to include `article_scores`**

Replace the existing `tmp_db` fixture:
```python
@pytest.fixture()
def tmp_db(tmp_path):
    """Return a fresh sqlite3 connection backed by a temp file."""
    db_path = tmp_path / "test_news.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            url TEXT PRIMARY KEY,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            article_count INTEGER,
            status TEXT,
            html TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS article_scores (
            url                 TEXT PRIMARY KEY,
            impact_score        INTEGER,
            authenticity_score  INTEGER,
            relevance_score     INTEGER,
            impact_reason       TEXT,
            authenticity_reason TEXT,
            relevance_reason    TEXT,
            cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    yield conn
    conn.close()
```

- [ ] **Step 2: Replace `test_new_urls_inserted_into_seen_articles` with a no-insert test**

Remove the old test and add:
```python
def test_fetcher_does_not_insert_seen_articles(tmp_db):
    """fetch_articles() must not write to seen_articles — that is main.py's responsibility."""
    from src.fetcher import fetch_articles

    new_url = "https://example.com/new-article"
    entry = _make_entry(new_url, hours_ago=1)
    feed = _make_feed([entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        fetch_articles()

    row = tmp_db.execute(
        "SELECT url FROM seen_articles WHERE url = ?", (new_url,)
    ).fetchone()
    assert row is None, "fetch_articles() must not insert into seen_articles"


def test_fetcher_prunes_article_scores(tmp_db):
    """fetch_articles() must delete article_scores rows older than 3 days."""
    from src.fetcher import fetch_articles

    stale_url = "https://example.com/stale"
    fresh_url = "https://example.com/fresh"

    tmp_db.execute(
        "INSERT INTO article_scores (url, impact_score, authenticity_score, relevance_score, "
        "impact_reason, authenticity_reason, relevance_reason, cached_at) "
        "VALUES (?, 5, 5, 5, 'r', 'r', 'r', datetime('now', '-4 days'))",
        (stale_url,),
    )
    tmp_db.execute(
        "INSERT INTO article_scores (url, impact_score, authenticity_score, relevance_score, "
        "impact_reason, authenticity_reason, relevance_reason, cached_at) "
        "VALUES (?, 8, 7, 9, 'r', 'r', 'r', datetime('now'))",
        (fresh_url,),
    )
    tmp_db.commit()

    with patch("src.fetcher.feedparser.parse", return_value=_make_feed([])), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        fetch_articles()

    stale_row = tmp_db.execute(
        "SELECT url FROM article_scores WHERE url = ?", (stale_url,)
    ).fetchone()
    fresh_row = tmp_db.execute(
        "SELECT url FROM article_scores WHERE url = ?", (fresh_url,)
    ).fetchone()
    assert stale_row is None, "Stale cache entry (>3 days) must be pruned"
    assert fresh_row is not None, "Fresh cache entry must be kept"
```

- [ ] **Step 3: Run new tests to confirm they fail**

```bash
poetry run pytest tests/test_fetcher.py::test_fetcher_does_not_insert_seen_articles tests/test_fetcher.py::test_fetcher_prunes_article_scores -v
```
Expected: FAIL

- [ ] **Step 4: Rewrite `src/fetcher.py` internal functions to return `list[dict]`**

Replace `_process_rss`:
```python
def _process_rss(
    feed_url: str,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    articles: list[dict] = []
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return articles

    publication = _domain(feed_url)

    for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
        url = getattr(entry, "link", None)
        if not url:
            continue
        published_at = _parse_struct_time(getattr(entry, "published_parsed", None))
        if not _is_recent(published_at, cutoff):
            continue
        if url in seen_urls:
            continue
        articles.append(_entry_to_dict(entry, url, publication, published_at))

    return articles
```

Replace `_process_hn`:
```python
def _process_hn(
    hn_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    articles: list[dict] = []
    try:
        resp = requests.get(
            hn_url,
            params={"numericFilters": f"created_at_i>{int(cutoff.timestamp())}"},
            timeout=10,
        )
        hits = resp.json().get("hits", [])
    except Exception:
        return articles

    publication = _domain(hn_url)

    for hit in hits[:MAX_ARTICLES_PER_SOURCE]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        if not url:
            continue
        published_at = _parse_iso(hit.get("created_at", ""))
        if not _is_recent(published_at, cutoff):
            continue
        if url in seen_urls:
            continue
        articles.append({
            "title": hit.get("title", ""),
            "url": url,
            "description": hit.get("story_text") or hit.get("comment_text") or "",
            "author": hit.get("author", ""),
            "publication": publication,
            "published_at": published_at,
        })

    return articles
```

Replace `_dispatch_feed`:
```python
def _dispatch_feed(
    feed_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    if feed_url == HN_ALGOLIA_URL:
        return _process_hn(feed_url, now, cutoff, seen_urls)
    return _process_rss(feed_url, cutoff, seen_urls)
```

Replace `_fetch_parallel`:
```python
def _fetch_parallel(
    seen_urls: set[str],
    cutoff: datetime,
    now: datetime,
) -> list[dict]:
    articles: list[dict] = []
    seen_in_run: set[str] = set()

    with ThreadPoolExecutor(max_workers=min(10, len(FEED_URLS))) as executor:
        futures = {
            executor.submit(_dispatch_feed, url, now, cutoff, seen_urls): url
            for url in FEED_URLS
        }
        for future in as_completed(futures):
            try:
                feed_articles = future.result()
            except Exception:
                continue
            for article in feed_articles:
                url = article["url"]
                if url not in seen_in_run:
                    seen_in_run.add(url)
                    articles.append(article)

    return articles
```

Replace `fetch_articles`:
```python
def fetch_articles() -> list[dict]:
    """Fetch and return new articles across all configured sources.

    For each source:
    * Parse published_at to a UTC-aware datetime.
    * Discard articles older than LOOKBACK_HOURS.
    * Skip URLs already present in seen_articles.

    Prunes seen_articles rows older than 7 days and article_scores older than
    SCORE_CACHE_TTL_DAYS. Does NOT write to seen_articles — that is main.py's
    responsibility (top-N only, after ranking).

    Returns a list of dicts with keys:
        title, url, description, author, publication, published_at
    """
    from src.config import SCORE_CACHE_TTL_DAYS

    conn = get_connection()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    prune_cutoff = now - timedelta(days=7)

    conn.execute(
        "DELETE FROM seen_articles WHERE seen_at < ?",
        (prune_cutoff.isoformat(),),
    )
    conn.execute(
        f"DELETE FROM article_scores WHERE cached_at < datetime('now', '-{SCORE_CACHE_TTL_DAYS} days')"
    )
    conn.commit()

    seen_urls: set[str] = {
        row[0]
        for row in conn.execute("SELECT url FROM seen_articles").fetchall()
    }
    conn.close()

    return _fetch_parallel(seen_urls, cutoff, now)
```

Note: remove the `LOOKBACK_HOURS` import from `src.config` at the top of the file since it's now imported inline (or keep the top-level import — either works; keep it at the top for consistency):

The import line at top of `fetcher.py` should be updated to include `SCORE_CACHE_TTL_DAYS`:
```python
from src.config import FEED_URLS, HN_ALGOLIA_URL, LOOKBACK_HOURS, MAX_ARTICLES_PER_SOURCE, SCORE_CACHE_TTL_DAYS
```
(Remove the inline `from src.config import SCORE_CACHE_TTL_DAYS` inside the function body.)

- [ ] **Step 5: Run all fetcher tests**

```bash
poetry run pytest tests/test_fetcher.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/fetcher.py tests/test_fetcher.py
git commit -m "feat: fetcher stops writing seen_articles; prunes article_scores cache"
```

---

## Task 4: Add score caching to `processor.py`

**Files:**
- Modify: `src/processor.py`
- Modify: `tests/test_processor.py`

**Context:** Add two module-level helpers: `_load_score_cache(conn)` reads all valid cache entries into a dict; `_write_score_cache(url, scores)` opens its own connection and persists scores (best-effort, like `_write_failed`). `process_articles()` loads the cache once before the loop and passes it to `_process_one()`.

- [ ] **Step 1: Add imports and fixture to `tests/test_processor.py`**

At the top of the file, add:
```python
import sqlite3
```

Add a new fixture after the existing helpers:
```python
@pytest.fixture()
def tmp_score_db(tmp_path):
    db_path = tmp_path / "scores.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE article_scores (
            url                 TEXT PRIMARY KEY,
            impact_score        INTEGER,
            authenticity_score  INTEGER,
            relevance_score     INTEGER,
            impact_reason       TEXT,
            authenticity_reason TEXT,
            relevance_reason    TEXT,
            cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE failed_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            url TEXT,
            title TEXT,
            publication TEXT,
            reason TEXT,
            failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    yield conn
    conn.close()
```

- [ ] **Step 2: Write failing tests**

Add `TestScoreCache` class to `tests/test_processor.py`:
```python
class TestScoreCache:
    """Score caching: cache hit skips LLM; cache miss calls LLM and writes cache."""

    def test_cache_hit_skips_llm(self, tmp_score_db):
        url = "https://example.com/cached"
        tmp_score_db.execute(
            "INSERT INTO article_scores "
            "(url, impact_score, authenticity_score, relevance_score, "
            "impact_reason, authenticity_reason, relevance_reason) "
            "VALUES (?, 8, 7, 9, 'big impact', 'credible source', 'on topic')",
            (url,),
        )
        tmp_score_db.commit()

        article = make_article(url=url)

        with patch("src.processor.get_connection", return_value=tmp_score_db), \
             patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_anthropic_cls.return_value.messages.create.assert_not_called()
        assert len(results) == 1
        assert results[0]["impact_score"] == 8
        assert results[0]["relevance_score"] == 9
        assert results[0]["impact_reason"] == "big impact"

    def test_cache_miss_calls_llm(self, tmp_score_db):
        article = make_article(url="https://example.com/new")
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("src.processor.get_connection", return_value=tmp_score_db), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]

    def test_cache_miss_writes_scores_to_db(self, tmp_score_db):
        url = "https://example.com/write-test"
        article = make_article(url=url)
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("src.processor.get_connection", return_value=tmp_score_db), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            process_articles([article])

        row = tmp_score_db.execute(
            "SELECT impact_score, relevance_score FROM article_scores WHERE url = ?", (url,)
        ).fetchone()
        assert row is not None
        assert row["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]
        assert row["relevance_score"] == VALID_CLAUDE_RESPONSE["relevance_score"]

    def test_expired_cache_entry_treated_as_miss(self, tmp_score_db):
        url = "https://example.com/expired"
        tmp_score_db.execute(
            "INSERT INTO article_scores "
            "(url, impact_score, authenticity_score, relevance_score, "
            "impact_reason, authenticity_reason, relevance_reason, cached_at) "
            "VALUES (?, 3, 3, 3, 'old', 'old', 'old', datetime('now', '-4 days'))",
            (url,),
        )
        tmp_score_db.commit()

        article = make_article(url=url)
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch("src.processor.get_connection", return_value=tmp_score_db), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = process_articles([article])

        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == VALID_CLAUDE_RESPONSE["impact_score"]
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
poetry run pytest tests/test_processor.py::TestScoreCache -v
```
Expected: FAIL — functions not yet implemented

- [ ] **Step 4: Add `_load_score_cache` to `src/processor.py`**

Add after the existing imports, before `SYSTEM_PROMPT`:
```python
from src.db import get_connection
```

Add after `_write_failed`:
```python
def _load_score_cache(conn) -> dict[str, dict]:
    """Load all non-expired score cache entries keyed by URL. Caller owns the connection."""
    from src.config import SCORE_CACHE_TTL_DAYS

    rows = conn.execute(
        f"SELECT url, impact_score, authenticity_score, relevance_score, "
        f"impact_reason, authenticity_reason, relevance_reason "
        f"FROM article_scores "
        f"WHERE cached_at >= datetime('now', '-{SCORE_CACHE_TTL_DAYS} days')"
    ).fetchall()
    return {
        row["url"]: {
            "impact_score": row["impact_score"],
            "authenticity_score": row["authenticity_score"],
            "relevance_score": row["relevance_score"],
            "impact_reason": row["impact_reason"],
            "authenticity_reason": row["authenticity_reason"],
            "relevance_reason": row["relevance_reason"],
        }
        for row in rows
    }


def _write_score_cache(url: str, scores: dict) -> None:
    """Persist computed scores to article_scores. Best-effort: never raises."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO article_scores "
                "(url, impact_score, authenticity_score, relevance_score, "
                "impact_reason, authenticity_reason, relevance_reason, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (
                    url,
                    scores["impact_score"],
                    scores["authenticity_score"],
                    scores["relevance_score"],
                    scores["impact_reason"],
                    scores["authenticity_reason"],
                    scores["relevance_reason"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
```

- [ ] **Step 5: Update `_process_one` to accept and use the cache**

Replace the existing `_process_one` signature and add cache logic:
```python
def _process_one(
    article: dict,
    client,
    use_local: bool,
    run_id: str | None = None,
    score_cache: dict[str, dict] | None = None,
) -> dict | None:
    """Process a single article with retry. Returns None on final failure (article dead-lettered)."""
    url = article.get("url", "")

    if score_cache and url in score_cache:
        return {**article, **score_cache[url]}

    user_content = (
        f"Author: {article['author']}\n"
        f"Publication: {article['publication']}\n"
        f"Title: {article['title']}\n"
        f"Description: {article['description']}"
    )
    last_exc: Exception | None = None
    for attempt in range(_cfg.PROCESSOR_MAX_RETRIES + 1):
        try:
            response_text = _call_local_llm(user_content) if use_local else _call_anthropic(client, user_content)
            parsed = json.loads(response_text)
            scores = {
                "impact_score": parsed["impact_score"],
                "authenticity_score": parsed["authenticity_score"],
                "relevance_score": parsed["relevance_score"],
                "impact_reason": parsed["impact_reason"],
                "authenticity_reason": parsed["authenticity_reason"],
                "relevance_reason": parsed["relevance_reason"],
            }
            _write_score_cache(url, scores)
            return {**article, **scores}
        except Exception as exc:
            last_exc = exc
            if attempt < _cfg.PROCESSOR_MAX_RETRIES:
                time.sleep(_cfg.PROCESSOR_RETRY_DELAY)

    _write_failed(article, str(last_exc), run_id)
    return None
```

- [ ] **Step 6: Update `process_articles` to load and pass the cache**

Replace the existing `process_articles`:
```python
def process_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Process articles by calling the configured LLM to add scores.

    Checks article_scores cache before each LLM call; writes to cache on success.
    Articles that fail after all retries are written to failed_articles and excluded.
    """
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    conn = get_connection()
    try:
        score_cache = _load_score_cache(conn)
    finally:
        conn.close()

    results = []
    with tqdm(total=len(articles), desc="Articles", unit="art", leave=False) as bar:
        for article in articles:
            result = _process_one(article, client, use_local, run_id, score_cache)
            if result is not None:
                results.append(result)
            bar.update(1)
    return results
```

- [ ] **Step 7: Run all processor tests**

```bash
poetry run pytest tests/test_processor.py -v
```
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: cache LLM scores in article_scores; skip LLM on cache hit"
```

---

## Task 5: Update `main.py` to write top-N URLs to `seen_articles` after ranking

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Read the existing `test_main.py` to understand the test pattern**

```bash
poetry run pytest tests/test_main.py -v
```
Read `tests/test_main.py` to understand how `run_pipeline` is tested and what mocks are in place.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_main.py`:
```python
def test_top_n_urls_written_to_seen_articles(tmp_path, monkeypatch):
    """After ranking, exactly the top-N article URLs must be inserted into seen_articles."""
    import sqlite3
    from unittest.mock import patch, MagicMock
    from src.main import run_pipeline

    # Build a minimal in-memory DB with all required tables
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    for ddl in [
        "CREATE TABLE seen_articles (url TEXT PRIMARY KEY, seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE runs (id TEXT PRIMARY KEY, started_at TIMESTAMP, completed_at TIMESTAMP, status TEXT, article_count INTEGER, html TEXT, error TEXT, dropped_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0)",
        "CREATE TABLE run_articles (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, title TEXT, url TEXT, publication TEXT, published_at TEXT, rank_score REAL)",
        "CREATE TABLE failed_articles (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, url TEXT, title TEXT, publication TEXT, reason TEXT, failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE filtered_articles (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, url TEXT, title TEXT, publication TEXT, relevance_score INTEGER, relevance_reason TEXT)",
        "CREATE TABLE article_scores (url TEXT PRIMARY KEY, impact_score INTEGER, authenticity_score INTEGER, relevance_score INTEGER, impact_reason TEXT, authenticity_reason TEXT, relevance_reason TEXT, cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    ]:
        conn.execute(ddl)
    conn.commit()

    top_urls = [f"https://example.com/article-{i}" for i in range(3)]

    ranked_articles = [
        {
            "url": url,
            "title": f"Title {i}",
            "publication": "example.com",
            "published_at": "2026-04-18T10:00:00+00:00",
            "rank_score": 9.0 - i,
            "impact_score": 9,
            "authenticity_score": 8,
            "relevance_score": 9,
            "summary": "A summary.",
        }
        for i, url in enumerate(top_urls)
    ]

    with patch("src.main.fetch_articles", return_value=[]), \
         patch("src.main.process_articles", return_value=ranked_articles), \
         patch("src.main.filter_articles", return_value=(ranked_articles, [])), \
         patch("src.main.rank_articles", return_value=ranked_articles), \
         patch("src.main.summarize_articles", return_value=ranked_articles), \
         patch("src.main.render_newsletter", return_value=("<html/>", "plain")), \
         patch("src.main.send_newsletter"), \
         patch("src.main.get_connection", return_value=conn):
        run_pipeline(dry_run=True, run_id="test-run-id")

    rows = conn.execute("SELECT url FROM seen_articles").fetchall()
    seen = {row["url"] for row in rows}
    for url in top_urls:
        assert url in seen, f"{url} must be in seen_articles after pipeline run"
    conn.close()
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
poetry run pytest tests/test_main.py::test_top_n_urls_written_to_seen_articles -v
```
Expected: FAIL

- [ ] **Step 4: Update `src/main.py`**

In `run_pipeline`, after the line `articles = rank_articles(articles)`, add:
```python
            now_iso = datetime.now(timezone.utc).isoformat()
            conn = get_connection()
            conn.executemany(
                "INSERT OR IGNORE INTO seen_articles (url, seen_at) VALUES (?, ?)",
                [(a["url"], now_iso) for a in articles],
            )
            conn.commit()
            conn.close()
```

The surrounding context in `main.py` should look like:
```python
            bar.set_description("Ranking")
            articles = rank_articles(articles)
            tqdm.write(f"[{run_id}] Ranked {len(articles)} articles")
            bar.update(1)

            now_iso = datetime.now(timezone.utc).isoformat()
            conn = get_connection()
            conn.executemany(
                "INSERT OR IGNORE INTO seen_articles (url, seen_at) VALUES (?, ?)",
                [(a["url"], now_iso) for a in articles],
            )
            conn.commit()
            conn.close()

            bar.set_description("Generating summaries")
            articles = summarize_articles(articles, run_id=run_id)
```

- [ ] **Step 5: Run all tests**

```bash
poetry run pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: write top-N ranked URLs to seen_articles in main pipeline"
```

---

## Task 6: Smoke test the full pipeline

- [ ] **Step 1: Run a dry-run and confirm the cache table is populated**

```bash
make run-dry 2>&1 | head -40
```
Expected: pipeline completes, articles processed, no errors

- [ ] **Step 2: Inspect the cache**

```bash
sqlite3 data/state.db "SELECT url, impact_score, relevance_score, cached_at FROM article_scores LIMIT 5;"
```
Expected: rows present with recent `cached_at` timestamps

- [ ] **Step 3: Run again and confirm cache hits**

```bash
make run-dry 2>&1 | grep -i "Articles"
```
Run `make run-dry` a second time. The second run should complete the "Articles" tqdm bar faster (cache hits skip LLM). If using `--clean`, seen_articles is cleared but `article_scores` persists, so scores are still cached.

- [ ] **Step 4: Inspect seen_articles contains only top-N**

```bash
sqlite3 data/state.db "SELECT COUNT(*) FROM seen_articles;"
```
Expected: ≤ 10 rows (one per top-N article per run, pruned after 7 days)
