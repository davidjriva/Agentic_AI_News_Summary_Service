# tqdm Pipeline Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tqdm progress bars to the CLI pipeline — a 5-stage bar in `main.py` and a per-article bar in `processor.py`.

**Architecture:** Add `tqdm` as a dependency, then wire a stage bar around the 5 pipeline calls in `run_pipeline()` and an article bar inside `process_articles()` using `executor.submit` + `as_completed`.

**Tech Stack:** tqdm, concurrent.futures.as_completed (stdlib)

---

## File Map

| File | Change |
|------|--------|
| `pyproject.toml` | Add `tqdm = "^4.0"` under `[tool.poetry.dependencies]` |
| `src/main.py` | Import tqdm; add 5-step stage bar; replace stage-boundary `log.info` with `tqdm.write` |
| `src/processor.py` | Import tqdm + `as_completed`; replace `executor.map` with `submit`/`as_completed`; wrap with article bar |

---

## Task 1: Add tqdm dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add tqdm to pyproject.toml**

In `pyproject.toml`, add after the `requests` line:

```toml
tqdm = "^4.0"
```

So the `[tool.poetry.dependencies]` block looks like:

```toml
[tool.poetry.dependencies]
python = "^3.11"
anthropic = "^0.40.0"
feedparser = "^6.0.11"
python-dotenv = "^1.0.0"
jinja2 = "^3.1.4"
requests = "^2.32.3"
tqdm = "^4.0"
fastapi = "^0.115.0"
uvicorn = {extras = ["standard"], version = "^0.30.0"}
```

- [ ] **Step 2: Install the new dependency**

```bash
poetry add tqdm
```

Expected: tqdm installed, `poetry.lock` updated.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "chore: add tqdm dependency"
```

---

## Task 2: Stage progress bar in main.py

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_main.py` (create if absent):

```python
from unittest.mock import patch, MagicMock
import pytest


def test_run_pipeline_uses_tqdm(tmp_path, monkeypatch):
    """Stage bar should be created with total=5 during a dry run."""
    import tqdm as tqdm_module

    bar_mock = MagicMock()
    bar_mock.__enter__ = MagicMock(return_value=bar_mock)
    bar_mock.__exit__ = MagicMock(return_value=False)
    tqdm_cls = MagicMock(return_value=bar_mock)

    with (
        patch("src.main.fetch_articles", return_value=[]),
        patch("src.main.process_articles", return_value=[]),
        patch("src.main.rank_articles", return_value=[]),
        patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
        patch("src.main.get_connection"),
        patch("src.main.tqdm", tqdm_cls),
    ):
        from src.main import run_pipeline
        run_pipeline(dry_run=True)

    tqdm_cls.assert_called_once_with(total=5, desc="Pipeline", leave=True)
    assert bar_mock.update.call_count == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_main.py::test_run_pipeline_uses_tqdm -v
```

Expected: FAIL — `AttributeError: module 'src.main' has no attribute 'tqdm'` (or ImportError)

- [ ] **Step 3: Implement stage bar in main.py**

Replace the current imports block and `run_pipeline` body in `src/main.py`:

**Imports — add after existing imports:**

```python
from tqdm import tqdm
```

**Inside `run_pipeline()`, replace the try block (lines 51–107) with:**

```python
    try:
        if clean:
            conn = get_connection()
            conn.execute(
                "DELETE FROM seen_articles WHERE seen_at >= datetime('now', '-12 hours')"
            )
            conn.commit()
            conn.close()
            tqdm.write(f"[{run_id}] Clean run: cleared seen_articles for past 12 hours")

        with tqdm(total=5, desc="Pipeline", leave=True) as bar:
            bar.set_description("Fetching articles")
            articles = fetch_articles()
            tqdm.write(f"[{run_id}] Fetched {len(articles)} articles")
            bar.update(1)

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
            bar.set_description(f"Processing with {provider_label}")
            articles = process_articles(articles)
            tqdm.write(f"[{run_id}] Processed {len(articles)} articles")
            bar.update(1)

            bar.set_description("Ranking")
            articles = rank_articles(articles)
            tqdm.write(f"[{run_id}] Ranked {len(articles)} articles")
            bar.update(1)

            bar.set_description("Rendering")
            html, plain_text = render_newsletter(articles, started_at)
            tqdm.write(f"[{run_id}] Newsletter rendered")
            bar.update(1)

            if dry_run:
                bar.set_description("Dry-run")
                tqdm.write(f"[{run_id}] Dry-run: printing HTML to stdout")
                print(html)
            else:
                bar.set_description("Sending email")
                send_newsletter(html, plain_text, started_at)
                tqdm.write(f"[{run_id}] Email sent")
            bar.update(1)

        completed_at = datetime.now(timezone.utc)
        conn = get_connection()
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, article_count=?, html=? WHERE id=?",
            ("success", completed_at.isoformat(), len(articles), html, run_id),
        )
        conn.commit()
        conn.close()

        tqdm.write(f"[{run_id}] Run complete")
        return run_id

    except Exception as exc:
        log.exception("[%s] Pipeline failed: %s", run_id, exc)
        completed_at = datetime.now(timezone.utc)
        conn = get_connection()
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, error=? WHERE id=?",
            ("error", completed_at.isoformat(), str(exc), run_id),
        )
        conn.commit()
        conn.close()
        raise
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_main.py::test_run_pipeline_uses_tqdm -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: add tqdm stage progress bar to main.py pipeline"
```

---

## Task 3: Article progress bar in processor.py

**Files:**
- Modify: `src/processor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_processor.py`:

```python
def test_process_articles_tqdm_bar(monkeypatch):
    """process_articles should tick tqdm once per article."""
    from unittest.mock import patch, MagicMock, call
    import src.config as _cfg

    articles = [
        {"title": "A", "author": "x", "publication": "p", "description": "d"},
        {"title": "B", "author": "y", "publication": "q", "description": "e"},
    ]

    good_response = '{"summary":"s","impact_score":7,"authenticity_score":6,"impact_reason":"r","authenticity_reason":"r"}'

    bar_mock = MagicMock()
    bar_mock.__enter__ = MagicMock(return_value=bar_mock)
    bar_mock.__exit__ = MagicMock(return_value=False)
    tqdm_cls = MagicMock(return_value=bar_mock)

    with (
        patch.object(_cfg, "LLM_PROVIDER", "local"),
        patch("src.processor.requests.post") as mock_post,
        patch("src.processor.tqdm", tqdm_cls),
    ):
        mock_post.return_value.json.return_value = {"choices": [{"message": {"content": good_response}}]}
        mock_post.return_value.raise_for_status = MagicMock()

        from src.processor import process_articles
        results = process_articles(articles)

    tqdm_cls.assert_called_once_with(
        total=2, desc="Articles", unit="art", leave=False
    )
    assert bar_mock.update.call_count == 2
    assert len(results) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_processor.py::test_process_articles_tqdm_bar -v
```

Expected: FAIL — `AttributeError: module 'src.processor' has no attribute 'tqdm'`

- [ ] **Step 3: Implement article bar in processor.py**

Replace the entire `src/processor.py` with:

```python
"""Article processor: calls an LLM to add summaries and scores to each article.

Supports two providers, selected via the LLM_PROVIDER config value:
  "anthropic" (default) — Anthropic SDK with prompt caching
  "local"               — local llama.cpp server via its OpenAI-compatible API
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import requests
from tqdm import tqdm

from src import config as _cfg

SYSTEM_PROMPT = """You are an AI news analyst. For each article provided, return a JSON object with exactly these keys:
- summary: a 2-3 sentence summary of the article
- impact_score: integer 1-10 rating of the article's impact on the AI field
- authenticity_score: integer 1-10 rating of the article's authenticity/credibility
- impact_reason: one-line rationale for the impact_score
- authenticity_reason: one-line rationale for the authenticity_score

Authenticity scoring rubric:
- Is the author a known researcher, practitioner, or credible journalist?
- Is the publication a primary source (lab blog, company announcement) or secondary (commentary, aggregator)?
- Is the content original research/reporting or opinion/repost?
- Unknown/anonymous authors score conservatively

Return ONLY a valid JSON object with no additional text."""


def _call_anthropic(client: anthropic.Anthropic, user_content: str) -> str:
    """Call the Anthropic API with prompt caching on the system message."""
    response = client.messages.create(
        model=_cfg.CLAUDE_MODEL,
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text


def _call_local_llm(user_content: str) -> str:
    """Call a local llama.cpp server via its OpenAI-compatible /v1/chat/completions endpoint."""
    response = requests.post(
        f"{_cfg.LOCAL_LLM_URL}/v1/chat/completions",
        json={
            "model": _cfg.LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 512,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _process_one(article: dict, client, use_local: bool) -> dict:
    """Process a single article — safe to call concurrently."""
    user_content = (
        f"Author: {article['author']}\n"
        f"Publication: {article['publication']}\n"
        f"Title: {article['title']}\n"
        f"Description: {article['description']}"
    )
    try:
        response_text = _call_local_llm(user_content) if use_local else _call_anthropic(client, user_content)
        parsed = json.loads(response_text)
        return {
            **article,
            "summary": parsed["summary"],
            "impact_score": parsed["impact_score"],
            "authenticity_score": parsed["authenticity_score"],
            "impact_reason": parsed["impact_reason"],
            "authenticity_reason": parsed["authenticity_reason"],
        }
    except Exception:
        return {
            **article,
            "impact_score": 5,
            "authenticity_score": 5,
            "summary": article["description"][:200],
            "impact_reason": "",
            "authenticity_reason": "",
        }


def process_articles(articles: list[dict]) -> list[dict]:
    """Process articles by calling the configured LLM to add summary and scores."""
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    results = []
    with ThreadPoolExecutor(max_workers=_cfg.PROCESSOR_MAX_WORKERS) as executor:
        futures = {executor.submit(_process_one, a, client, use_local): a for a in articles}
        with tqdm(total=len(articles), desc="Articles", unit="art", leave=False) as bar:
            for future in as_completed(futures):
                results.append(future.result())
                bar.update(1)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_processor.py::test_process_articles_tqdm_bar -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: add tqdm article progress bar to processor.py"
```
