# Relevance Filtering & Feed Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate off-topic articles by (1) targeting AI-specific feed URLs, (2) adding LLM-scored relevance gating with dead-letter queue for failures, (3) surfacing dropped/failed articles in the run detail dashboard, and (4) upgrading the local LLM to a larger model.

**Architecture:** A new `relevance_score` (1–10) field is added to the processor's LLM prompt at no extra API cost; articles scoring below `RELEVANCE_THRESHOLD=6` are filtered out by a new `src/filter.py` step inserted between processor and ranker. Filtered and failed articles are stored in two new SQLite tables and displayed in the run detail view. Feed URLs are replaced with AI-specific category feeds.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (via existing `src/db.py`), Anthropic SDK, llama.cpp / Ollama (`qwen3:30b`), Jinja2, pytest

---

## Parallel Execution Map

```
Branch A (Feed URL targeting)     ─┐
Branch B (Relevance + Retry + DLQ) ─┤─ run in parallel
Branch C (Local LLM upgrade)      ─┘
Branch D (Filter + Dashboard)      ── run after Branch B completes
```

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/config.py` | Modify | Add `RELEVANCE_THRESHOLD`, `PROCESSOR_MAX_RETRIES`, `PROCESSOR_RETRY_DELAY`; update feed URLs; extend schema docs |
| `src/db.py` | Modify | Add `failed_articles` and `filtered_articles` tables; add `dropped_count`/`failed_count` columns to `runs` |
| `src/processor.py` | Modify | Add `relevance_score`/`relevance_reason` to LLM prompt; add retry loop; write to `failed_articles` on exhaustion; accept `run_id` param |
| `src/filter.py` | **Create** | `filter_articles(articles) -> tuple[list[dict], list[dict]]` — splits kept vs dropped by `RELEVANCE_THRESHOLD` |
| `src/main.py` | Modify | Insert `filter_articles` between processor and ranker; persist dropped/failed counts to `runs` table |
| `src/server.py` | Modify | Expose `filtered_articles` and `failed_articles` in `GET /runs/{run_id}` |
| `templates/run_detail.html.jinja2` | Modify | Add "Filtered Out" and "Processing Failures" collapsible sections |
| `templates/dashboard.html.jinja2` | Modify | Show `dropped_count` and `failed_count` columns in run list |
| `ModelFile.qwen` | Modify | Upgrade base model from `qwen3:14b` to `qwen3:30b` |
| `tests/test_processor.py` | Modify | Update for new schema fields; add retry and dead-letter tests |
| `tests/test_filter.py` | **Create** | Tests for `filter_articles` |

---

## Branch A: Feed URL Targeting

**Rationale:** Wired and The Verge currently use full-site feeds (returning general tech news). VentureBeat's AI category feed (`/category/ai/feed/`) is stale as of Jan 2026. Replace/supplement with AI-specific URLs.

**Candidate replacements (verify each before committing):**
- The Verge AI: `https://www.theverge.com/rss/ai-artificial-intelligence/index.xml`
- Wired AI: `https://www.wired.com/feed/tag/artificial-intelligence/rss`
- VentureBeat AI (stale — replace with primary sources):
  - `https://openai.com/blog/rss.xml`
  - `https://huggingface.co/blog/feed.xml`

### Task A1: Verify feed URLs

**Files:**
- No file changes — pure verification step

- [ ] **Step 1: Test each candidate URL with feedparser in a Python shell**

Run the following in `poetry run python`:

```python
import feedparser

urls = [
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.wired.com/feed/tag/artificial-intelligence/rss",
    "https://openai.com/blog/rss.xml",
    "https://huggingface.co/blog/feed.xml",
]

for url in urls:
    feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0 (compatible; AgenticAINewsBot/1.0)"})
    entries = feed.entries
    status = feed.get("status", "unknown")
    recent = entries[0].get("title", "N/A") if entries else "EMPTY"
    print(f"{url}\n  status={status} entries={len(entries)} latest='{recent}'\n")
```

Expected: each URL returns `status=200` and at least 1 entry. Flag any that return 0 entries or non-200 status — do not include them in Task A2.

- [ ] **Step 2: Record results**

Note which URLs are confirmed working. If `theverge.com` or `wired.com` block (status 403/blocked), keep the current full-site feeds for those sources — the relevance filter added in Branch B will handle off-topic articles.

---

### Task A2: Update feed configuration

**Files:**
- Modify: `src/config.py:6-18`

- [ ] **Step 1: Update `FEED_URLS` in `src/config.py`**

Replace the current `FEED_URLS` list with verified URLs. The exact substitutions depend on Task A1 results, but the target state is:

```python
FEED_URLS = [
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.LG",                                      # cs.LG = Machine Learning
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",  # AI section (verify A1)
    "https://www.wired.com/feed/tag/artificial-intelligence/rss",          # AI tag (verify A1)
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://openai.com/blog/rss.xml",                                    # replaces stale VentureBeat
    "https://huggingface.co/blog/feed.xml",                               # replaces stale VentureBeat
    "https://news.google.com/rss/search?q=agentic+AI",
    "https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI",  # JSON, not RSS
]
```

Rules:
- If a URL from A1 returned 0 entries or non-200: keep the existing full-site URL for that source with a comment explaining why.
- Do not remove `HN_ALGOLIA_URL` — the fetcher handles it separately.
- `cs.LG` (Machine Learning arxiv category) is a net-new addition; always include it.

- [ ] **Step 2: Update `HN_ALGOLIA_URL` comment if needed**

`HN_ALGOLIA_URL` stays the same. No change needed unless the constant drifts.

- [ ] **Step 3: Run the fetcher smoke-test**

```bash
poetry run python -c "
from src.fetcher import fetch_articles
arts = fetch_articles()
from collections import Counter
pubs = Counter(a['publication'] for a in arts)
for pub, n in sorted(pubs.items()):
    print(f'{n:3d}  {pub}')
print('Total:', len(arts))
"
```

Expected: output shows publications from the new sources (e.g. `openai.com`, `huggingface.co`, `cs.LG`). No crash.

- [ ] **Step 4: Commit**

```bash
git add src/config.py
git commit -m "feat: target AI-specific feed URLs; add cs.LG and primary sources"
```

---

## Branch B: Relevance Scoring + Retry + Dead-letter Queue

### Task B1: Add new DB tables and `runs` columns

**Files:**
- Modify: `src/db.py`

- [ ] **Step 1: Write failing tests for new tables**

In `tests/test_db.py` (create if it doesn't exist):

```python
import sqlite3
import pytest
from src.db import get_connection


def test_failed_articles_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='failed_articles'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_filtered_articles_table_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='filtered_articles'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_runs_has_dropped_count_column(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db._DATA_DIR", tmp_path)
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(runs)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "dropped_count" in cols
    assert "failed_count" in cols
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_db.py -v
```

Expected: FAIL — tables and columns don't exist yet.

- [ ] **Step 3: Add tables and columns to `src/db.py`**

Add three new SQL constants after `_CREATE_RUN_ARTICLES`:

```python
_CREATE_FAILED_ARTICLES = """
CREATE TABLE IF NOT EXISTS failed_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    url         TEXT,
    title       TEXT,
    publication TEXT,
    reason      TEXT,
    failed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_FILTERED_ARTICLES = """
CREATE TABLE IF NOT EXISTS filtered_articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    url              TEXT,
    title            TEXT,
    publication      TEXT,
    relevance_score  INTEGER,
    relevance_reason TEXT
);
"""
```

In `get_connection()`, add after the existing `conn.execute(_CREATE_RUN_ARTICLES)` line:

```python
    conn.execute(_CREATE_FAILED_ARTICLES)
    conn.execute(_CREATE_FILTERED_ARTICLES)
    # Migrate existing runs table — safe to run repeatedly
    for col_sql in (
        "ALTER TABLE runs ADD COLUMN dropped_count INTEGER DEFAULT 0",
        "ALTER TABLE runs ADD COLUMN failed_count INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(col_sql)
        except Exception:
            pass  # column already exists
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/test_db.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: add failed_articles and filtered_articles tables; add dropped/failed counts to runs"
```

---

### Task B2: Add `relevance_score` to the processor prompt

**Files:**
- Modify: `src/config.py` (schema docs + new constants)
- Modify: `src/processor.py` (SYSTEM_PROMPT + response parsing)

- [ ] **Step 1: Write failing tests for new schema fields**

In `tests/test_processor.py`, add to the existing `VALID_CLAUDE_RESPONSE` dict and `TestValidJsonResponse` class:

```python
# Add to VALID_CLAUDE_RESPONSE at top of file:
VALID_CLAUDE_RESPONSE = {
    "summary": "Anthropic researchers demonstrate agentic AI completing complex tasks. The study shows significant improvements in reliability. This marks a key milestone for the field.",
    "impact_score": 8,
    "authenticity_score": 7,
    "impact_reason": "Significant advancement in agentic AI reliability with broad implications.",
    "authenticity_reason": "Published by named researcher at credible AI lab.",
    "relevance_score": 9,
    "relevance_reason": "Directly about agentic AI systems and autonomous task completion.",
}


# Add new test class:
class TestRelevanceScore:
    def test_relevance_score_in_result(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["relevance_score"] == 9

    def test_relevance_reason_in_result(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert results[0]["relevance_reason"] == VALID_CLAUDE_RESPONSE["relevance_reason"]

    def test_relevance_score_in_system_prompt(self):
        from src.processor import SYSTEM_PROMPT
        assert "relevance_score" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_processor.py::TestRelevanceScore -v
```

Expected: FAIL.

- [ ] **Step 3: Update `SYSTEM_PROMPT` in `src/processor.py`**

Replace the current `SYSTEM_PROMPT` string:

```python
SYSTEM_PROMPT = """You are an AI news analyst specializing in agentic AI, machine learning, and deep learning. For each article provided, return a JSON object with exactly these keys:
- summary: a 2-3 sentence summary of the article
- impact_score: integer 1-10 rating of the article's impact on the AI/ML field
- authenticity_score: integer 1-10 rating of the article's authenticity/credibility
- relevance_score: integer 1-10 rating of how directly relevant this article is to agentic AI, machine learning, or deep learning (1 = completely unrelated, 10 = core topic)
- impact_reason: one-line rationale for the impact_score
- authenticity_reason: one-line rationale for the authenticity_score
- relevance_reason: one-line rationale for the relevance_score

Relevance scoring rubric:
- Score 8-10: Directly about agentic AI systems, LLM research, ML model training/deployment, deep learning breakthroughs, AI safety
- Score 5-7: Adjacent topics — AI in business/product, general ML tooling, AI policy with technical substance
- Score 1-4: Tangentially AI-related (e.g. tech company news, crypto, general software, climate tech)

Authenticity scoring rubric:
- Is the author a known researcher, practitioner, or credible journalist?
- Is the publication a primary source (lab blog, company announcement) or secondary (commentary, aggregator)?
- Is the content original research/reporting or opinion/repost?
- Unknown/anonymous authors score conservatively

Return ONLY a valid JSON object with no additional text."""
```

- [ ] **Step 4: Update `_process_one` to parse and return the new fields**

In the `try` block of `_process_one`, update the return dict:

```python
return {
    **article,
    "summary": parsed["summary"],
    "impact_score": parsed["impact_score"],
    "authenticity_score": parsed["authenticity_score"],
    "relevance_score": parsed["relevance_score"],
    "impact_reason": parsed["impact_reason"],
    "authenticity_reason": parsed["authenticity_reason"],
    "relevance_reason": parsed["relevance_reason"],
}
```

- [ ] **Step 5: Update `src/config.py` schema docs and add new constants**

Add after the existing config constants (before the schema comment block):

```python
RELEVANCE_THRESHOLD: int = int(os.getenv("RELEVANCE_THRESHOLD", "6"))
PROCESSOR_MAX_RETRIES: int = int(os.getenv("PROCESSOR_MAX_RETRIES", "2"))
PROCESSOR_RETRY_DELAY: float = float(os.getenv("PROCESSOR_RETRY_DELAY", "2.0"))
```

In the schema docs comment block, add under "Added by processor.py":

```python
#   relevance_score:     int   — 1–10; LLM-assigned relevance to agentic AI/ML
#   relevance_reason:    str   — one-line rationale for relevance_score
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
poetry run pytest tests/test_processor.py::TestRelevanceScore -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/processor.py src/config.py tests/test_processor.py
git commit -m "feat: add relevance_score and relevance_reason to processor LLM prompt"
```

---

### Task B3: Add retry logic and dead-letter queue to processor

**Files:**
- Modify: `src/processor.py`

- [ ] **Step 1: Write failing tests for retry + dead-letter behavior**

Add to `tests/test_processor.py`:

```python
import time
from unittest.mock import call, patch


class TestRetryAndDeadLetter:
    def test_retries_on_api_failure_before_giving_up(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("transient error")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = process_articles([article])

        assert mock_client.messages.create.call_count == 3  # 1 attempt + 2 retries
        mock_dlq.assert_called_once()
        assert len(results) == 0  # failed article excluded

    def test_succeeds_on_second_attempt(self):
        article = make_article()
        good_response = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))
        fail_response = MagicMock()
        fail_response.messages.create.side_effect = Exception("transient")

        call_count = {"n": 0}

        def side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("transient")
            return good_response.messages.create(**kwargs)

        mock_client = _make_mock_client(json.dumps(VALID_CLAUDE_RESPONSE))
        mock_client.messages.create.side_effect = side_effect

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 2), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = process_articles([article])

        assert len(results) == 1
        assert results[0]["relevance_score"] == VALID_CLAUDE_RESPONSE["relevance_score"]

    def test_dead_letter_receives_article_url_and_reason(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("api down")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        written = {}

        def capture_dlq(article, reason, run_id):
            written["url"] = article["url"]
            written["reason"] = reason

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed", side_effect=capture_dlq):
            process_articles([article])

        assert written["url"] == article["url"]
        assert "api down" in written["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_processor.py::TestRetryAndDeadLetter -v
```

Expected: FAIL — `_write_failed` and retry logic don't exist yet.

- [ ] **Step 3: Rewrite `_process_one` in `src/processor.py` with retry + dead-letter**

Add `import time` at the top. Add `_write_failed` helper. Replace `_process_one`:

```python
import time
from src.db import get_connection


def _write_failed(article: dict, reason: str, run_id: str | None) -> None:
    """Persist a failed article to the dead-letter queue."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO failed_articles (run_id, url, title, publication, reason) VALUES (?, ?, ?, ?, ?)",
        (run_id, article.get("url", ""), article.get("title", ""), article.get("publication", ""), reason),
    )
    conn.commit()
    conn.close()


def _process_one(article: dict, client, use_local: bool, run_id: str | None = None) -> dict | None:
    """Process a single article with retry. Returns None on final failure (article dead-lettered)."""
    last_exc: Exception | None = None
    for attempt in range(_cfg.PROCESSOR_MAX_RETRIES + 1):
        try:
            user_content = (
                f"Author: {article['author']}\n"
                f"Publication: {article['publication']}\n"
                f"Title: {article['title']}\n"
                f"Description: {article['description']}"
            )
            response_text = _call_local_llm(user_content) if use_local else _call_anthropic(client, user_content)
            parsed = json.loads(response_text)
            return {
                **article,
                "summary": parsed["summary"],
                "impact_score": parsed["impact_score"],
                "authenticity_score": parsed["authenticity_score"],
                "relevance_score": parsed["relevance_score"],
                "impact_reason": parsed["impact_reason"],
                "authenticity_reason": parsed["authenticity_reason"],
                "relevance_reason": parsed["relevance_reason"],
            }
        except Exception as exc:
            last_exc = exc
            if attempt < _cfg.PROCESSOR_MAX_RETRIES:
                time.sleep(_cfg.PROCESSOR_RETRY_DELAY)

    _write_failed(article, str(last_exc), run_id)
    return None
```

- [ ] **Step 4: Update `process_articles` to accept `run_id` and filter `None` results**

```python
def process_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Process articles by calling the configured LLM to add summary and scores.

    Articles that fail after all retries are written to failed_articles and excluded.
    """
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    with ThreadPoolExecutor(max_workers=_cfg.PROCESSOR_MAX_WORKERS) as executor:
        futures = {executor.submit(_process_one, a, client, use_local, run_id): a for a in articles}
        results = []
        with tqdm(total=len(articles), desc="Articles", unit="art") as bar:
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)
                bar.update(1)
        return results
```

- [ ] **Step 5: Run all processor tests**

```bash
poetry run pytest tests/test_processor.py -v
```

Expected: all PASS. Note: existing `TestFallbackOnMalformedResponse` tests will now fail because the fallback path was removed — delete those tests (the dead-letter queue replaces that behavior) OR update them to mock `_write_failed` and assert `len(results) == 0`.

Update `TestFallbackOnMalformedResponse` tests: replace assertions that check for `impact_score=5` fallback with assertions that the article is excluded from results and `_write_failed` is called:

```python
class TestFallbackOnMalformedResponse:
    def test_malformed_json_excludes_article(self):
        article = make_article()
        mock_client = _make_mock_client("This is not valid JSON at all!")

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0

    def test_api_exception_excludes_article(self):
        article = make_article()
        mock_messages = MagicMock()
        mock_messages.create.side_effect = Exception("API error")
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed"):
            results = process_articles([article])

        assert len(results) == 0
```

- [ ] **Step 6: Run all tests**

```bash
poetry run pytest tests/test_processor.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: add retry logic and dead-letter queue to processor; remove fallback scores"
```

---

## Branch C: Local LLM Model Upgrade

**Rationale:** The current `qwen3:14b` is a capable model but `qwen3:30b` (~17-18 GB at Q4_K_M quantization) fits comfortably in 24 GB unified memory and provides meaningfully better structured JSON output and relevance scoring. Both use the same Ollama interface — no code changes required.

### Task C1: Upgrade the Ollama model

**Files:**
- Modify: `ModelFile.qwen`

- [ ] **Step 1: Update `ModelFile.qwen`**

Change `FROM qwen3:14b` to `FROM qwen3:30b`:

```
# Use 30B model — fits in 24GB unified memory (~17-18GB Q4_K_M)
FROM qwen3:30b

# Set the 32k context window
PARAMETER num_ctx 32768

# Helps model digest system prompt faster
PARAMETER num_batch 512

# Set temperature to 0 for consistent results
PARAMETER temperature 0

# Senior FDE Personality
SYSTEM """You are a Senior Forward Deployed Engineer specialized in Agentic AI. 
You are currently working on the 'Agentic_AI_News_Summary_Service'. 
Your goal is to build robust, asynchronous news ingestion and summarization pipelines.
Prefer functional programming and clear type definitions."""
```

- [ ] **Step 2: Pull and register the new model**

Run in terminal (this downloads ~17 GB — run only when on a good connection):

```bash
ollama pull qwen3:30b
ollama create agentic-news -f ModelFile.qwen
```

Verify the model loads:

```bash
ollama run agentic-news "Return JSON: {\"relevance_score\": 9}" --nowordwrap
```

Expected: model responds with valid JSON containing `relevance_score`.

- [ ] **Step 3: Commit**

```bash
git add ModelFile.qwen
git commit -m "feat: upgrade local LLM from qwen3:14b to qwen3:30b for 24GB unified memory"
```

---

## Branch D: Relevance Filter + Dashboard Visibility

> **Depends on Branch B completing Tasks B1–B3 first** (needs `relevance_score` field in articles, `filtered_articles` table in DB).

### Task D1: Create `src/filter.py`

**Files:**
- Create: `src/filter.py`
- Create: `tests/test_filter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_filter.py`:

```python
import pytest
from unittest.mock import patch
import src.config as _cfg
from src.filter import filter_articles


def make_article(relevance_score: int, url: str = "http://example.com") -> dict:
    return {
        "url": url,
        "title": "Test Article",
        "publication": "example.com",
        "relevance_score": relevance_score,
        "relevance_reason": "test reason",
        "impact_score": 7,
        "authenticity_score": 7,
        "summary": "summary",
        "impact_reason": "",
        "authenticity_reason": "",
    }


class TestFilterArticles:
    def test_returns_tuple_of_kept_and_dropped(self):
        articles = [make_article(8), make_article(3)]
        kept, dropped = filter_articles(articles)
        assert isinstance(kept, list)
        assert isinstance(dropped, list)

    def test_article_at_threshold_is_kept(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(6)])
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_article_below_threshold_is_dropped(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(5)])
        assert len(kept) == 0
        assert len(dropped) == 1

    def test_article_above_threshold_is_kept(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(9)])
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_mixed_articles_split_correctly(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            articles = [
                make_article(9, "http://a.com"),
                make_article(4, "http://b.com"),
                make_article(6, "http://c.com"),
                make_article(2, "http://d.com"),
            ]
            kept, dropped = filter_articles(articles)
        assert {a["url"] for a in kept} == {"http://a.com", "http://c.com"}
        assert {a["url"] for a in dropped} == {"http://b.com", "http://d.com"}

    def test_empty_input_returns_empty_tuples(self):
        kept, dropped = filter_articles([])
        assert kept == []
        assert dropped == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/test_filter.py -v
```

Expected: FAIL — `src/filter.py` doesn't exist.

- [ ] **Step 3: Create `src/filter.py`**

```python
"""Relevance filter: splits processed articles into kept and dropped."""
from src import config as _cfg


def filter_articles(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split articles by relevance_score threshold.

    Returns:
        (kept, dropped) — kept articles score >= RELEVANCE_THRESHOLD; dropped score below.
    """
    kept, dropped = [], []
    for article in articles:
        if article.get("relevance_score", 0) >= _cfg.RELEVANCE_THRESHOLD:
            kept.append(article)
        else:
            dropped.append(article)
    return kept, dropped
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/test_filter.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/filter.py tests/test_filter.py
git commit -m "feat: add relevance filter splitting kept and dropped articles"
```

---

### Task D2: Wire filter into the main pipeline

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Update imports in `src/main.py`**

Add to the existing imports:

```python
from src.filter import filter_articles
```

- [ ] **Step 2: Update `run_pipeline` to insert filter step and track counts**

Replace the processing + ranking block in `run_pipeline` (the section from `process_articles` call through `rank_articles` call):

```python
        provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
        bar.set_description(f"Processing with {provider_label}")
        bar.update(1)
        articles = process_articles(articles, run_id=run_id)
        tqdm_module.tqdm.write(f"[{run_id}] Processed {len(articles)} articles")

        bar.set_description("Filtering by relevance")
        bar.update(1)
        articles, dropped = filter_articles(articles)
        tqdm_module.tqdm.write(f"[{run_id}] Kept {len(articles)}, dropped {len(dropped)} articles")

        if dropped:
            conn = get_connection()
            conn.executemany(
                "INSERT INTO filtered_articles (run_id, url, title, publication, relevance_score, relevance_reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (run_id, a["url"], a["title"], a["publication"],
                     a.get("relevance_score", 0), a.get("relevance_reason", ""))
                    for a in dropped
                ],
            )
            conn.commit()
            conn.close()

        bar.set_description("Ranking")
        bar.update(1)
        articles = rank_articles(articles)
        tqdm_module.tqdm.write(f"[{run_id}] Ranked {len(articles)} articles")
```

Also update the progress bar `total` from `5` to `6` (new filter step added):

```python
        bar = tqdm(total=6, leave=True)
```

Update the `UPDATE runs` SQL at the end to query and persist counts:

```python
        # Count failures written to dead-letter for this run
        conn = get_connection()
        failed_count_row = conn.execute(
            "SELECT COUNT(*) FROM failed_articles WHERE run_id=?", (run_id,)
        ).fetchone()
        failed_count = failed_count_row[0] if failed_count_row else 0

        completed_at = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, article_count=?, html=?, dropped_count=?, failed_count=? WHERE id=?",
            ("success", completed_at.isoformat(), len(articles), html, len(dropped), failed_count, run_id),
        )
        conn.commit()
        conn.close()
```

Note: `dropped` must be in scope at this point — move it to the outer `try` scope by initializing `dropped: list[dict] = []` before the `fetch_articles()` call.

- [ ] **Step 3: Initialize `dropped` before the try block**

At the top of the `try` block (after `if clean:` section), add:

```python
        dropped: list[dict] = []
```

- [ ] **Step 4: Smoke test the pipeline**

```bash
poetry run python -m src.main --dry-run 2>&1 | tail -20
```

Expected: output includes lines like `Kept N, dropped M articles`. No crash.

- [ ] **Step 5: Commit**

```bash
git add src/main.py
git commit -m "feat: insert filter_articles step into pipeline; persist dropped/failed counts to runs"
```

---

### Task D3: Expose filtered/failed data in run detail dashboard

**Files:**
- Modify: `src/server.py`
- Modify: `templates/run_detail.html.jinja2`
- Modify: `templates/dashboard.html.jinja2`

- [ ] **Step 1: Update `GET /runs/{run_id}` in `src/server.py` to query filtered and failed articles**

In `get_run()`, add queries for the new tables after `source_rows`:

```python
    filtered_rows = conn.execute(
        "SELECT title, url, publication, relevance_score, relevance_reason "
        "FROM filtered_articles WHERE run_id=? ORDER BY relevance_score DESC",
        (run_id,),
    ).fetchall()
    failed_rows = conn.execute(
        "SELECT title, url, publication, reason FROM failed_articles WHERE run_id=?",
        (run_id,),
    ).fetchall()
    conn.close()
```

Pass them to the template:

```python
    return templates.TemplateResponse(
        request,
        "run_detail.html.jinja2",
        {
            "run_id": run_id,
            "started_at": row["started_at"],
            "sources": sources,
            "filtered": [dict(r) for r in filtered_rows],
            "failed": [dict(r) for r in failed_rows],
        },
    )
```

- [ ] **Step 2: Add "Filtered Out" and "Processing Failures" sections to `templates/run_detail.html.jinja2`**

Read the current template first, then add the following sections after the existing sources sidebar (before the closing `</body>` tag or at the end of the main content area):

```html
{% if filtered %}
<details style="margin: 1rem 0; border: 1px solid #dedede; border-radius: 4px; padding: 0.5rem 1rem;">
  <summary style="cursor: pointer; font-family: 'Helvetica Neue', sans-serif; font-size: 0.8rem; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #888;">
    Filtered Out ({{ filtered|length }} articles — below relevance threshold)
  </summary>
  <table style="width:100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.85rem; font-family: Georgia, serif;">
    <thead>
      <tr style="border-bottom: 1px solid #dedede; text-align: left;">
        <th style="padding: 4px 8px;">Title</th>
        <th style="padding: 4px 8px; width: 120px;">Source</th>
        <th style="padding: 4px 8px; width: 80px;">Score</th>
        <th style="padding: 4px 8px;">Reason</th>
      </tr>
    </thead>
    <tbody>
    {% for art in filtered %}
      <tr style="border-bottom: 1px solid #f0f0f0;">
        <td style="padding: 4px 8px;"><a href="{{ art.url }}" target="_blank" style="color: #333;">{{ art.title }}</a></td>
        <td style="padding: 4px 8px; color: #888;">{{ art.publication }}</td>
        <td style="padding: 4px 8px; text-align: center;">{{ art.relevance_score }}/10</td>
        <td style="padding: 4px 8px; color: #888; font-style: italic;">{{ art.relevance_reason }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</details>
{% endif %}

{% if failed %}
<details style="margin: 1rem 0; border: 1px solid #f5c6cb; border-radius: 4px; padding: 0.5rem 1rem; background: #fff8f8;">
  <summary style="cursor: pointer; font-family: 'Helvetica Neue', sans-serif; font-size: 0.8rem; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #c41230;">
    Processing Failures ({{ failed|length }})
  </summary>
  <table style="width:100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.85rem; font-family: Georgia, serif;">
    <thead>
      <tr style="border-bottom: 1px solid #f5c6cb; text-align: left;">
        <th style="padding: 4px 8px;">Title</th>
        <th style="padding: 4px 8px; width: 120px;">Source</th>
        <th style="padding: 4px 8px;">Error</th>
      </tr>
    </thead>
    <tbody>
    {% for art in failed %}
      <tr style="border-bottom: 1px solid #fde8ea;">
        <td style="padding: 4px 8px;"><a href="{{ art.url }}" target="_blank" style="color: #333;">{{ art.title }}</a></td>
        <td style="padding: 4px 8px; color: #888;">{{ art.publication }}</td>
        <td style="padding: 4px 8px; color: #c41230; font-size: 0.8rem;">{{ art.reason }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</details>
{% endif %}
```

- [ ] **Step 3: Update `templates/dashboard.html.jinja2` to show dropped/failed counts in the run list**

Find the table row where run metadata is rendered (the `{% for run in runs %}` loop). Add `dropped_count` and `failed_count` cells. The exact location depends on the current template structure — add two `<td>` cells after the `article_count` cell:

```html
<td style="...">{{ run.dropped_count or 0 }} filtered</td>
<td style="...">{{ run.failed_count or 0 }} failed</td>
```

Also add corresponding `<th>` headers in the table header row.

- [ ] **Step 4: Start the server and verify the run detail page**

```bash
make serve
```

Open `http://localhost:8000` in a browser. Trigger a run via the dashboard UI. After completion, click through to the run detail page. Verify:
- "Filtered Out" section appears and is expandable (collapsed by default)
- "Processing Failures" section only appears if there are failures
- Dashboard run list shows `N filtered` and `N failed` columns

- [ ] **Step 5: Commit**

```bash
git add src/server.py templates/run_detail.html.jinja2 templates/dashboard.html.jinja2
git commit -m "feat: show filtered and failed articles in run detail dashboard"
```

---

## Self-Review

**Spec coverage:**
- ✅ Feed URL targeting (Branch A)
- ✅ `relevance_score` added to LLM prompt (Task B2)
- ✅ Retry mechanism with configurable retries/delay (Task B3)
- ✅ Dead-letter queue for failed articles (Tasks B1 + B3)
- ✅ Relevance filter step in pipeline (Tasks D1 + D2)
- ✅ Dropped articles visible in dashboard (Task D3)
- ✅ Failed articles visible in dashboard (Task D3)
- ✅ Local LLM model upgrade to qwen3:30b (Branch C)
- ✅ `RELEVANCE_THRESHOLD` configurable via env var (Task B2)

**Type consistency:**
- `filter_articles` returns `tuple[list[dict], list[dict]]` — used as `articles, dropped = filter_articles(articles)` in `main.py` ✅
- `process_articles(articles, run_id=run_id)` — `run_id` is in scope in `run_pipeline` ✅
- `_write_failed(article, reason, run_id)` — called from `_process_one` with same signature ✅
- `filtered_articles` table schema matches insert in `main.py` and query in `server.py` ✅
