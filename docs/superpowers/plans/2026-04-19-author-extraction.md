# Author Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximize author coverage by applying regex extraction in `fetcher.py` and piggybacking author extraction onto the existing LLM summary call in `processor.py`.

**Architecture:** Two-stage extraction — Stage 1 applies a regex helper `_extract_author` inside `_entry_to_dict` when feedparser returns no author; Stage 2 extends `SUMMARY_SYSTEM_PROMPT` to return an optional `author` field and applies it in `summarize_articles` only when the article author is still blank.

**Tech Stack:** Python stdlib `re`, `feedparser`, `anthropic` SDK, `pytest`, `unittest.mock`

---

## Execution waves

```
Wave 1 (parallel): Task 1 ──┐
                   Task 2 ──┴── Wave 2: Task 3 (regression gate)
```

- **Task 1** and **Task 2** touch entirely different files — run them in parallel.
- **Task 3** runs only after both Wave 1 tasks complete.

---

## File Map

| File | Task | Change |
|------|------|--------|
| `src/fetcher.py` | 1 | Add `import re`, `_BYLINE_RE`, `_extract_author`; update `_entry_to_dict` |
| `tests/test_fetcher.py` | 1 | Append `TestExtractAuthor` and `TestEntryToDictAuthorExtraction` |
| `src/processor.py` | 2 | Replace `SUMMARY_SYSTEM_PROMPT`; update `summarize_articles` |
| `tests/test_processor.py` | 2 | Append 3 tests to `TestSummarizeArticles` |

---

### Task 1: Regex author extraction in `src/fetcher.py` [WAVE 1 — parallel with Task 2]

**Files:**
- Modify: `src/fetcher.py`
- Modify: `tests/test_fetcher.py`

#### Background (read before editing)

`src/fetcher.py` contains a helper block ending with `_is_recent` (~line 65), then `fetch_articles` starts. `_entry_to_dict` is at the bottom (~line 310). The current imports at the top do NOT include `import re`.

`tests/test_fetcher.py` already imports `SimpleNamespace`, `datetime`, `timedelta`, `timezone` from the standard library. New test classes can be appended at the end of the file.

---

- [ ] **Step 1: Add `import re` to `src/fetcher.py`**

Open `src/fetcher.py`. The existing import block looks like:

```python
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
```

Insert `import re` immediately after `import calendar`:

```python
import calendar
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
```

- [ ] **Step 2: Add `_BYLINE_RE` and `_extract_author` to `src/fetcher.py`**

In `src/fetcher.py`, locate the line:

```python
def _is_recent(published_at: datetime | None, cutoff: datetime) -> bool:
```

After the entire `_is_recent` function body (the function ends with `return published_at >= cutoff`), insert the following block — before the `# Core fetch function` section comment:

```python
_BYLINE_RE = re.compile(
    r"""
    (?:
        [Bb]y\s+                              # "By " or "by "
      | [Ww]ritten\s+by\s+                    # "Written by " or "written by "
      | ^\s*[—\-]\s+                          # "— " or "- " at line start
      | \|\s*                                 # "| "
    )
    (
        [A-Z][a-z]+(?:[- ][A-Z][a-z]+){1,3}  # 2–4 title-cased words / hyphenated
    )
    """,
    re.VERBOSE | re.MULTILINE,
)


def _extract_author(description: str) -> str:
    """Return the first author name found in *description* via byline regex, or ''."""
    if not description:
        return ""
    text = re.sub(r"<[^>]+>", "", description)
    m = _BYLINE_RE.search(text)
    return m.group(1) if m else ""
```

- [ ] **Step 3: Update `_entry_to_dict` in `src/fetcher.py`**

Find the existing `_entry_to_dict` function, which currently reads:

```python
def _entry_to_dict(
    entry,
    url: str,
    publication: str,
    published_at: datetime,
) -> dict:
    """Convert a feedparser entry to a normalised article dict."""
    return {
        "title": getattr(entry, "title", ""),
        "url": url,
        "description": getattr(entry, "summary", "") or getattr(entry, "description", "") or "",
        "author": getattr(entry, "author", ""),
        "publication": publication,
        "published_at": published_at,
    }
```

Replace it entirely with:

```python
def _entry_to_dict(
    entry,
    url: str,
    publication: str,
    published_at: datetime,
) -> dict:
    """Convert a feedparser entry to a normalised article dict."""
    author = getattr(entry, "author", "")
    description = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    if not author:
        author = _extract_author(description)
    return {
        "title": getattr(entry, "title", ""),
        "url": url,
        "description": description,
        "author": author,
        "publication": publication,
        "published_at": published_at,
    }
```

- [ ] **Step 4: Append tests to `tests/test_fetcher.py`**

Open `tests/test_fetcher.py` and append the following at the very end of the file:

```python
# ---------------------------------------------------------------------------
# Test: _extract_author
# ---------------------------------------------------------------------------
from src.fetcher import _extract_author


class TestExtractAuthor:
    """_extract_author parses bylines from RSS description text."""

    def test_by_prefix(self):
        assert _extract_author("By Alex Wilhelm — some intro text") == "Alex Wilhelm"

    def test_written_by_prefix(self):
        assert _extract_author("Written by Sarah Chen-Moore, staff writer") == "Sarah Chen-Moore"

    def test_dash_prefix_at_line_start(self):
        assert _extract_author("— Maria Lopez\nSome article content here") == "Maria Lopez"

    def test_pipe_separator(self):
        assert _extract_author("Tech News | Jordan Kim | April 2026") == "Jordan Kim"

    def test_no_match_returns_empty(self):
        assert _extract_author("No byline information at all.") == ""

    def test_empty_string_returns_empty(self):
        assert _extract_author("") == ""

    def test_html_description_with_byline(self):
        assert _extract_author('<p>By <strong>Alex Wilhelm</strong></p>') == "Alex Wilhelm"


# ---------------------------------------------------------------------------
# Test: _entry_to_dict author extraction
# ---------------------------------------------------------------------------
from src.fetcher import _entry_to_dict


class TestEntryToDictAuthorExtraction:
    """_entry_to_dict calls _extract_author when feedparser returns no author."""

    def _now(self):
        return datetime.now(timezone.utc)

    def test_feedparser_author_used_when_present(self):
        entry = SimpleNamespace(
            title="Test", link="http://x.com/a", author="Existing Author",
            summary="By Someone Else", description="",
        )
        result = _entry_to_dict(entry, "http://x.com/a", "x.com", self._now())
        assert result["author"] == "Existing Author"

    def test_regex_fallback_when_author_empty(self):
        entry = SimpleNamespace(
            title="Test", link="http://x.com/b", author="",
            summary="By Alex Wilhelm — intro", description="",
        )
        result = _entry_to_dict(entry, "http://x.com/b", "x.com", self._now())
        assert result["author"] == "Alex Wilhelm"

    def test_author_blank_when_no_regex_match(self):
        entry = SimpleNamespace(
            title="Test", link="http://x.com/c", author="",
            summary="No byline here at all.", description="",
        )
        result = _entry_to_dict(entry, "http://x.com/c", "x.com", self._now())
        assert result["author"] == ""
```

- [ ] **Step 5: Run the new tests**

```
poetry run pytest tests/test_fetcher.py::TestExtractAuthor tests/test_fetcher.py::TestEntryToDictAuthorExtraction -v
```

Expected: all 10 tests PASS.

- [ ] **Step 6: Run full fetcher suite for regressions**

```
poetry run pytest tests/test_fetcher.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fetcher.py tests/test_fetcher.py
git commit -m "feat: add _extract_author regex helper; apply in _entry_to_dict"
```

---

### Task 2: LLM fallback author in `src/processor.py` [WAVE 1 — parallel with Task 1]

**Files:**
- Modify: `src/processor.py`
- Modify: `tests/test_processor.py`

#### Background (read before editing)

`src/processor.py` has a `SUMMARY_SYSTEM_PROMPT` string constant (~line 43) and a `summarize_articles` function (~line 272). Inside `summarize_articles`, after calling the LLM, there is a `try/except` block that parses JSON and sets `summary`. The `TestSummarizeArticles` class in `tests/test_processor.py` already uses helper `_make_processed_article()` and `_make_mock_client()`.

---

- [ ] **Step 1: Replace `SUMMARY_SYSTEM_PROMPT` in `src/processor.py`**

Find the existing constant:

```python
SUMMARY_SYSTEM_PROMPT = """You are an AI news journalist writing for a technical audience. Write a detailed editorial paragraph summarizing the provided article.

Cover all of the following in 4-6 sentences:
- Who: the organization, researchers, or individuals involved
- What: what was released, discovered, or announced — with concrete specifics
- When / Where: timing and context of origin
- Why it matters: concrete significance and implications for the AI/ML field

Return ONLY a valid JSON object with one key:
- summary: the full paragraph (continuous prose, no line breaks, no bullet points)"""
```

Replace it with:

```python
SUMMARY_SYSTEM_PROMPT = """You are an AI news journalist writing for a technical audience. Write a detailed editorial paragraph summarizing the provided article.

Cover all of the following in 4-6 sentences:
- Who: the organization, researchers, or individuals involved
- What: what was released, discovered, or announced — with concrete specifics
- When / Where: timing and context of origin
- Why it matters: concrete significance and implications for the AI/ML field

Return ONLY a valid JSON object with these keys:
- summary: the full paragraph (continuous prose, no line breaks, no bullet points)
- author: the article author's name if clearly identifiable from the title or description; otherwise an empty string"""
```

- [ ] **Step 2: Update the JSON-parsing block inside `summarize_articles` in `src/processor.py`**

Inside `summarize_articles`, find this block:

```python
                        try:
                            parsed = json.loads(text)
                            summary = parsed["summary"]
                        except (json.JSONDecodeError, KeyError):
                            # Local LLMs often return plain prose instead of JSON — use it directly
                            if text and text.strip():
                                summary = text.strip()
                            else:
                                raise ValueError("empty LLM response")
                        article = {**article, "summary": summary}
                        break
```

Replace it with:

```python
                        try:
                            parsed = json.loads(text)
                            summary = parsed["summary"]
                            llm_author = parsed.get("author", "") or ""
                        except (json.JSONDecodeError, KeyError):
                            # Local LLMs often return plain prose instead of JSON — use it directly
                            if text and text.strip():
                                summary = text.strip()
                            else:
                                raise ValueError("empty LLM response")
                            llm_author = ""
                        article = {**article, "summary": summary}
                        if llm_author and not article.get("author"):
                            article = {**article, "author": llm_author}
                        break
```

- [ ] **Step 3: Append 3 tests to `TestSummarizeArticles` in `tests/test_processor.py`**

Find the class `TestSummarizeArticles` in `tests/test_processor.py`. Locate its final test method (currently `test_local_provider_called_for_summaries`). Append the following three methods inside the class (same indentation level as the other methods):

```python
    def test_llm_author_applied_when_article_author_blank(self):
        article = {**_make_processed_article(), "author": ""}
        payload = json.dumps({"summary": "Full paragraph.", "author": "Alex Wilhelm"})
        mock_client = _make_mock_client(payload)

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["author"] == "Alex Wilhelm"

    def test_llm_author_does_not_overwrite_existing_author(self):
        article = {**_make_processed_article(), "author": "Original Author"}
        payload = json.dumps({"summary": "Full paragraph.", "author": "Alex Wilhelm"})
        mock_client = _make_mock_client(payload)

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["author"] == "Original Author"

    def test_author_stays_blank_when_llm_returns_empty(self):
        article = {**_make_processed_article(), "author": ""}
        payload = json.dumps({"summary": "Full paragraph.", "author": ""})
        mock_client = _make_mock_client(payload)

        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = summarize_articles([article])

        assert results[0]["author"] == ""
```

- [ ] **Step 4: Run the new tests**

```
poetry run pytest tests/test_processor.py::TestSummarizeArticles::test_llm_author_applied_when_article_author_blank tests/test_processor.py::TestSummarizeArticles::test_llm_author_does_not_overwrite_existing_author tests/test_processor.py::TestSummarizeArticles::test_author_stays_blank_when_llm_returns_empty -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run full processor suite for regressions**

```
poetry run pytest tests/test_processor.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: LLM fallback author extraction in summarize_articles"
```

---

### Task 3: Regression gate [WAVE 2 — after Tasks 1 and 2 complete]

**Files:** none changed — read-only verification.

- [ ] **Step 1: Run the full test suite**

```
poetry run pytest tests/ -v
```

Expected: all tests PASS. If any test fails, report the failure output verbatim and stop — do not attempt fixes.
