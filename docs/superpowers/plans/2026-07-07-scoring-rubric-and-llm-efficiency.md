# Scoring Rubric + Two-Stage LLM Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single per-article scoring call into a cheap relevance triage over all articles plus a full-rubric scoring pass over only the survivors, add anchored impact/authenticity rubrics, fold relevance into ranking, version the score cache by prompt, and add a golden-set eval harness.

**Architecture:** `processor.py` splits `process_articles()` into `triage_articles()` (relevance only, all articles) and `score_articles()` (impact + authenticity, gate survivors only). `main.py` runs `fetch → triage → filter → score → rank → …`. Local LLM calls gain grammar-constrained JSON (`response_format`) and 500-char description truncation. `article_scores` gains `triage_version`/`score_version` columns so a prompt change invalidates stale cache rows.

**Tech Stack:** Python 3, SQLAlchemy + Alembic (Supabase Postgres), llama.cpp OpenAI-compatible API / Anthropic SDK, pytest + testcontainers, `just` task runner.

## Global Constraints

- **No LLM calls / no `just eval` execution while the in-flight pipeline run holds the llama.cpp server** (`:8089`). Task 8 creates the eval harness but must NOT run it until the user confirms the server is free.
- Article dict is an **additive contract** — each stage appends fields, never removes prior ones (documented in `src/config.py`).
- Cache correctness: a cached score is only reused when its stage's prompt-version hash matches the current code.
- Failure semantics unchanged: triage/scoring failures dead-letter to `failed_articles` and are excluded; summary failures fall back to cleaned RSS description (never drop an article).
- Tests mock at external boundaries (`requests.post`, `anthropic.Anthropic`, `feedparser.parse`, `smtplib.SMTP`); patch `src.config` via `patch.object(_cfg, …)`. DB-touching tests request the `db` fixture (testcontainers; requires Docker). Test schema is built from `Base.metadata.create_all()` in `tests/conftest.py`, so new model columns apply automatically — the Alembic migration is production-only and is not exercised by CI.
- Local llama.cpp payloads keep `chat_template_kwargs: {enable_thinking: False}` on every call.

---

### Task 1: Cache-version columns on `article_scores`

**Files:**
- Modify: `src/models.py:87-99` (ArticleScore)
- Create: `alembic/versions/<hash>_add_score_cache_versions.py`
- Test: `tests/test_processor.py` (new `TestCacheVersionColumns` class)

**Interfaces:**
- Produces: `ArticleScore.triage_version: str | None`, `ArticleScore.score_version: str | None` (both `Text`, nullable).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_processor.py`:

```python
class TestCacheVersionColumns:
    def test_article_score_has_version_columns(self, db):
        from src.models import ArticleScore
        url = "https://example.com/versioned"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=8, authenticity_score=7, relevance_score=9,
                impact_reason="r", authenticity_reason="r", relevance_reason="r",
                triage_version="abc123", score_version="def456",
            ))
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.triage_version == "abc123"
        assert row.score_version == "def456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestCacheVersionColumns -v`
Expected: FAIL — `TypeError: 'triage_version' is an invalid keyword argument for ArticleScore`

- [ ] **Step 3: Add the columns to the model**

In `src/models.py`, inside `class ArticleScore`, after the `relevance_reason` column and before `cached_at`:

```python
    # Short hash of the triage / scoring system prompt in effect when this row
    # was written. A cache read only reuses a value when its stage's hash still
    # matches the current code, so a rubric change auto-invalidates stale rows.
    triage_version: Mapped[str | None] = mapped_column(Text)
    score_version: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test-one tests/test_processor.py::TestCacheVersionColumns -v`
Expected: PASS

- [ ] **Step 5: Write the Alembic migration (production-only; do not run against prod here)**

Create `alembic/versions/a1b2c3d4e5f6_add_score_cache_versions.py`:

```python
"""add score cache version columns

Revision ID: a1b2c3d4e5f6
Revises: 5c6a06c70a64
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5c6a06c70a64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('article_scores', sa.Column('triage_version', sa.Text(), nullable=True))
    op.add_column('article_scores', sa.Column('score_version', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('article_scores', 'score_version')
    op.drop_column('article_scores', 'triage_version')
```

Note: `down_revision` must be the current head. Verify with `just db-current` if a container/DB is available; otherwise confirm `5c6a06c70a64` is the latest file whose `down_revision` nothing else points to (it is, per `alembic/versions/`).

- [ ] **Step 6: Commit**

```bash
git add src/models.py alembic/versions/a1b2c3d4e5f6_add_score_cache_versions.py tests/test_processor.py
git commit -m "feat: add triage_version/score_version cache columns to article_scores"
```

---

### Task 2: Rubric prompts, version hashes, and JSON schemas

**Files:**
- Modify: `src/processor.py:26-56` (replace `SYSTEM_PROMPT`; keep `SUMMARY_SYSTEM_PROMPT`)
- Test: `tests/test_processor.py` (new `TestRubricPrompts` class)

**Interfaces:**
- Produces:
  - `TRIAGE_SYSTEM_PROMPT: str`, `SCORING_SYSTEM_PROMPT: str` (module constants)
  - `TRIAGE_VERSION: str`, `SCORE_VERSION: str` (12-char sha256 hex of the respective prompt)
  - `_TRIAGE_SCHEMA: dict`, `_SCORE_SCHEMA: dict` (JSON-schema dicts for `response_format`)
  - `_prompt_hash(text: str) -> str`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_processor.py`:

```python
class TestRubricPrompts:
    def test_triage_prompt_is_relevance_only(self):
        from src.processor import TRIAGE_SYSTEM_PROMPT
        assert "relevance_score" in TRIAGE_SYSTEM_PROMPT
        assert "impact_score" not in TRIAGE_SYSTEM_PROMPT

    def test_scoring_prompt_has_anchored_impact_bands(self):
        from src.processor import SCORING_SYSTEM_PROMPT
        assert "impact_score" in SCORING_SYSTEM_PROMPT
        assert "authenticity_score" in SCORING_SYSTEM_PROMPT
        # Calibration guard against 6-8 clustering
        assert "4-6" in SCORING_SYSTEM_PROMPT

    def test_version_hashes_are_short_hex(self):
        from src.processor import TRIAGE_VERSION, SCORE_VERSION
        assert len(TRIAGE_VERSION) == 12
        assert len(SCORE_VERSION) == 12
        assert TRIAGE_VERSION != SCORE_VERSION

    def test_version_hash_tracks_prompt(self):
        from src.processor import _prompt_hash, TRIAGE_SYSTEM_PROMPT, TRIAGE_VERSION
        assert _prompt_hash(TRIAGE_SYSTEM_PROMPT) == TRIAGE_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestRubricPrompts -v`
Expected: FAIL — `ImportError: cannot import name 'TRIAGE_SYSTEM_PROMPT'`

- [ ] **Step 3: Replace the prompt block in `src/processor.py`**

Add `import hashlib` at the top with the other stdlib imports. Replace the single `SYSTEM_PROMPT = """…"""` block (lines 26-45) with:

```python
TRIAGE_SYSTEM_PROMPT = """You are an AI news analyst specializing in agentic AI, machine learning, and deep learning. For the article provided, judge only how directly relevant it is to those topics. Return a JSON object with exactly these keys:
- relevance_score: integer 1-10 (1 = completely unrelated, 10 = core topic)
- relevance_reason: one-line rationale for the relevance_score

Relevance scoring rubric:
- Score 8-10: Directly about agentic AI systems, LLM research, ML model training/deployment, deep learning breakthroughs, AI safety
- Score 5-7: Adjacent topics — AI in business/product, general ML tooling, AI policy with technical substance
- Score 1-4: Tangentially AI-related (e.g. tech company news, crypto, general software, climate tech)

Return ONLY a valid JSON object with no additional text."""

SCORING_SYSTEM_PROMPT = """You are an AI news analyst specializing in agentic AI, machine learning, and deep learning. For the article provided, rate its impact and authenticity. Return a JSON object with exactly these keys:
- impact_score: integer 1-10 rating of the article's impact on the AI/ML field
- authenticity_score: integer 1-10 rating of the article's authenticity/credibility
- impact_reason: one-line rationale for the impact_score
- authenticity_reason: one-line rationale for the authenticity_score

Impact scoring rubric:
- Score 9-10: Frontier model releases or field-moving research (new SOTA, major capability or paradigm shift)
- Score 7-8: Notable models, widely-useful tools, or significant papers
- Score 5-6: Incremental research, ecosystem/tooling news with real substance
- Score 3-4: Routine business, funding, or product news
- Score 1-2: Negligible relevance or substance
Calibration: most articles score 4-6; reserve 8+ for genuinely field-moving news.

Authenticity scoring rubric:
- Score 9-10: Primary source (lab blog, company announcement) or peer-reviewed venue by a named researcher/practitioner
- Score 7-8: Credible journalist or established outlet reporting original material
- Score 5-6: Secondary commentary or analysis with clear attribution
- Score 3-4: Anonymous authorship, aggregators, or reposts
- Score 1-2: Unverifiable or low-credibility content
Unknown/anonymous authors score conservatively.

Return ONLY a valid JSON object with no additional text."""


def _prompt_hash(text: str) -> str:
    """12-char stable hash identifying a prompt version for cache invalidation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


TRIAGE_VERSION = _prompt_hash(TRIAGE_SYSTEM_PROMPT)
SCORE_VERSION = _prompt_hash(SCORING_SYSTEM_PROMPT)

_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {"type": "integer", "minimum": 1, "maximum": 10},
        "relevance_reason": {"type": "string"},
    },
    "required": ["relevance_score", "relevance_reason"],
}

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "impact_score": {"type": "integer", "minimum": 1, "maximum": 10},
        "authenticity_score": {"type": "integer", "minimum": 1, "maximum": 10},
        "impact_reason": {"type": "string"},
        "authenticity_reason": {"type": "string"},
    },
    "required": ["impact_score", "authenticity_score", "impact_reason", "authenticity_reason"],
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test-one tests/test_processor.py::TestRubricPrompts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: split scoring into triage + scoring rubric prompts with version hashes"
```

---

### Task 3: Tightened LLM call helpers (truncation + grammar-constrained JSON)

**Files:**
- Modify: `src/processor.py` (replace `_call_anthropic`/`_call_local_llm`, add `_build_user_content`)
- Test: `tests/test_processor.py` (new `TestCallHelpers` class)

**Interfaces:**
- Produces:
  - `TRIAGE_DESC_CHARS = 500`, `SCORE_DESC_CHARS = 1900` (per-stage description caps)
  - `_build_user_content(article: dict, max_desc: int = 500) -> str`
  - `_call_local(system_prompt: str, user_content: str, max_tokens: int, schema: dict) -> str`
  - `_call_anthropic(client, system_prompt: str, user_content: str, max_tokens: int) -> str`
- Consumes: `TRIAGE_SYSTEM_PROMPT`, `_TRIAGE_SCHEMA` (Task 2).

**Rationale for the per-stage caps (see spec §2):** measured feed data shows
only arXiv descriptions exceed ~400 chars (median ~1,430, arXiv-capped at
1,920), so the cap only bites arXiv. Relevance is settled by the lead
sentences → 500 for triage (which runs on *all* articles). Impact/authenticity
claims live in the abstract's tail (Sun et al. 2019, BERT classification) → the
full abstract (1,900) for scoring, which runs only on gate survivors and is a
no-op for every non-arXiv source.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_processor.py`:

```python
class TestCallHelpers:
    def test_build_user_content_default_truncates_at_500(self):
        from src.processor import _build_user_content
        article = make_article(description="x" * 2000)
        content = _build_user_content(article)
        # default 500-char cap on description
        assert "x" * 500 in content
        assert "x" * 501 not in content

    def test_build_user_content_respects_max_desc(self):
        from src.processor import _build_user_content, SCORE_DESC_CHARS
        assert SCORE_DESC_CHARS == 1900
        article = make_article(description="y" * 2500)
        content = _build_user_content(article, SCORE_DESC_CHARS)
        assert "y" * 1900 in content
        assert "y" * 1901 not in content

    def test_stage_caps_defined(self):
        from src.processor import TRIAGE_DESC_CHARS, SCORE_DESC_CHARS
        assert TRIAGE_DESC_CHARS == 500
        assert SCORE_DESC_CHARS == 1900

    def test_local_call_includes_response_format_schema(self):
        from src.processor import _call_local, _TRIAGE_SCHEMA
        mock_resp = _make_local_mock_response('{"relevance_score": 8, "relevance_reason": "r"}')
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            _call_local("SYS", "USER", 128, _TRIAGE_SCHEMA)
        payload = mock_post.call_args[1]["json"]
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["schema"] == _TRIAGE_SCHEMA
        assert payload["max_tokens"] == 128
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_anthropic_call_passes_system_and_max_tokens(self):
        from src.processor import _call_anthropic
        mock_client = _make_mock_client('{"relevance_score": 8, "relevance_reason": "r"}')
        _call_anthropic(mock_client, "SYS PROMPT", "USER", 128)
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 128
        assert kwargs["system"][0]["text"] == "SYS PROMPT"
        assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestCallHelpers -v`
Expected: FAIL — `ImportError: cannot import name '_build_user_content'`

- [ ] **Step 3: Replace the call helpers in `src/processor.py`**

Delete the old `_call_anthropic`, `_call_local_llm`, `_call_anthropic_summary`, `_call_local_llm_summary` bodies for scoring (keep the summary ones as-is — Task 5 note) and add the generalized helpers. Insert after the schema constants:

```python
# Per-stage description caps. Only arXiv descriptions exceed ~400 chars
# (arXiv-capped at 1,920), so these only affect arXiv abstracts: relevance is
# settled by the lead sentences (500), impact/authenticity claims live in the
# abstract tail so scoring gets the full abstract (1,900), and the summary
# likewise reads better from the full abstract than the first third.
TRIAGE_DESC_CHARS = 500
SCORE_DESC_CHARS = 1900
SUMMARY_DESC_CHARS = 1900


def _build_user_content(article: dict, max_desc: int = 500) -> str:
    """Assemble the user message, truncating the description to bound prompt size."""
    description = (article.get("description", "") or "")[:max_desc]
    return (
        f"Author: {article.get('author', '')}\n"
        f"Publication: {article.get('publication', '')}\n"
        f"Title: {article.get('title', '')}\n"
        f"Description: {description}"
    )


def _call_local(system_prompt: str, user_content: str, max_tokens: int, schema: dict) -> str:
    """Call the local llama.cpp server with grammar-constrained JSON output."""
    response = requests.post(
        f"{_cfg.LOCAL_LLM_URL}/v1/chat/completions",
        json={
            "model": _cfg.LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "scores", "schema": schema},
            },
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_anthropic(client: anthropic.Anthropic, system_prompt: str, user_content: str, max_tokens: int) -> str:
    """Call the Anthropic API with ephemeral prompt caching on the system message."""
    response = client.messages.create(
        model=_cfg.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text
```

Keep the existing `_call_local_llm_summary` and `_call_anthropic_summary` functions unchanged (still used by `summarize_articles`).

- [ ] **Step 4: Run test to verify it passes**

Run: `just test-one tests/test_processor.py::TestCallHelpers -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: generalized LLM call helpers with truncation and constrained JSON"
```

---

### Task 4: `triage_articles()` with versioned relevance cache

**Files:**
- Modify: `src/processor.py` (replace `_load_score_cache`, `_write_score_cache`, `_process_one`, `process_articles`; add triage functions)
- Test: `tests/test_processor.py` (replace `process_articles` tests with triage equivalents)

**Interfaces:**
- Produces:
  - `_load_score_cache() -> dict[str, dict]` — per-URL dict of all 6 score fields + `triage_version` + `score_version`
  - `_write_triage_cache(url: str, fields: dict) -> None` — upserts `relevance_score`, `relevance_reason`, `triage_version`
  - `triage_articles(articles: list[dict], run_id: str | None = None) -> list[dict]` — adds `relevance_score`/`relevance_reason`; dead-letters failures
  - `_TRIAGE_FIELDS = ("relevance_score", "relevance_reason")`
- Consumes: `_build_user_content`, `_call_local`, `_call_anthropic`, `TRIAGE_SYSTEM_PROMPT`, `_TRIAGE_SCHEMA`, `TRIAGE_VERSION`.

- [ ] **Step 1: Write the failing tests**

Replace the classes `TestValidJsonResponse`, `TestFallbackOnMalformedResponse`, `TestPromptCachingSystemMessage`, `TestLocalLLMProvider`, `TestArticleTqdmBar`, `TestRelevanceScore`, `TestRetryAndDeadLetter` (all of which call the removed `process_articles`) — migrate their intent to `triage_articles`. Add:

```python
TRIAGE_RESPONSE = {"relevance_score": 9, "relevance_reason": "Directly about agentic AI systems."}


class TestTriageArticles:
    def test_relevance_merged_into_article(self):
        article = make_article()
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = triage_articles([article])
        assert results[0]["relevance_score"] == 9
        assert results[0]["relevance_reason"] == TRIAGE_RESPONSE["relevance_reason"]
        assert results[0]["title"] == article["title"]

    def test_local_provider_used_for_triage(self):
        article = make_article()
        mock_resp = _make_local_mock_response(json.dumps(TRIAGE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            results = triage_articles([article])
        assert results[0]["relevance_score"] == 9
        assert mock_post.call_args[1]["json"]["max_tokens"] == 128

    def test_failure_dead_letters_and_excludes(self):
        article = make_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("api down")
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = triage_articles([article])
        assert len(results) == 0
        mock_dlq.assert_called_once()


class TestTriageCache:
    def test_cache_hit_skips_llm_when_version_matches(self, db):
        from src.processor import TRIAGE_VERSION
        url = "https://example.com/triage-cached"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, relevance_score=9, relevance_reason="cached",
                triage_version=TRIAGE_VERSION,
            ))
        with patch("anthropic.Anthropic") as mock_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = triage_articles([make_article(url=url)])
        mock_cls.return_value.messages.create.assert_not_called()
        assert results[0]["relevance_score"] == 9

    def test_stale_version_treated_as_miss(self, db):
        url = "https://example.com/triage-stale"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, relevance_score=3, relevance_reason="old",
                triage_version="OLDVERSION00",
            ))
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = triage_articles([make_article(url=url)])
        mock_client.messages.create.assert_called_once()
        assert results[0]["relevance_score"] == 9

    def test_cache_miss_writes_triage_version(self, db):
        url = "https://example.com/triage-write"
        from src.processor import TRIAGE_VERSION
        mock_client = _make_mock_client(json.dumps(TRIAGE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            triage_articles([make_article(url=url)])
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.relevance_score == 9
        assert row.triage_version == TRIAGE_VERSION
```

Update the import line at the top of the file:
`from src.processor import triage_articles, score_articles, summarize_articles`
(add `score_articles` now; it lands in Task 5 — until then keep `triage_articles, summarize_articles` and add `score_articles` in Task 5's step 1.)

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestTriageArticles -v`
Expected: FAIL — `ImportError: cannot import name 'triage_articles'`

- [ ] **Step 3: Rewrite the cache + triage functions in `src/processor.py`**

Replace `_SCORE_FIELDS`, `_load_score_cache`, `_write_score_cache`, `_process_one`, `process_articles` with:

```python
_TRIAGE_FIELDS = ("relevance_score", "relevance_reason")
_SCORE_FIELDS = ("impact_score", "authenticity_score", "impact_reason", "authenticity_reason")
_ALL_CACHE_FIELDS = _TRIAGE_FIELDS + _SCORE_FIELDS + ("triage_version", "score_version")


def _load_score_cache() -> dict[str, dict]:
    """Load all non-expired score cache rows keyed by URL (all fields + versions)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_cfg.SCORE_CACHE_TTL_DAYS)
    with get_session() as session:
        rows = session.execute(
            select(ArticleScore).where(ArticleScore.cached_at >= cutoff)
        ).scalars()
        return {row.url: {f: getattr(row, f) for f in _ALL_CACHE_FIELDS} for row in rows}


def _write_triage_cache(url: str, fields: dict) -> None:
    """Upsert relevance fields + triage_version. Best-effort: never raises."""
    try:
        values = {"url": url, "cached_at": func.now(), "triage_version": TRIAGE_VERSION,
                  **{f: fields[f] for f in _TRIAGE_FIELDS}}
        stmt = pg_insert(ArticleScore).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ArticleScore.url],
            set_={"cached_at": func.now(), "triage_version": TRIAGE_VERSION,
                  **{f: fields[f] for f in _TRIAGE_FIELDS}},
        )
        with get_session() as session:
            session.execute(stmt)
    except Exception:
        pass


def _triage_one(article, client, use_local, run_id=None, score_cache=None) -> dict | None:
    url = article.get("url", "")
    if score_cache and url in score_cache:
        cached = score_cache[url]
        if cached.get("relevance_score") is not None and cached.get("triage_version") == TRIAGE_VERSION:
            return {**article, **{f: cached[f] for f in _TRIAGE_FIELDS}}

    user_content = _build_user_content(article, TRIAGE_DESC_CHARS)
    last_exc: Exception | None = None
    for attempt in range(_cfg.PROCESSOR_MAX_RETRIES + 1):
        try:
            text = (_call_local(TRIAGE_SYSTEM_PROMPT, user_content, 128, _TRIAGE_SCHEMA)
                    if use_local else _call_anthropic(client, TRIAGE_SYSTEM_PROMPT, user_content, 128))
            parsed = json.loads(text)
            fields = {f: parsed[f] for f in _TRIAGE_FIELDS}
            _write_triage_cache(url, fields)
            return {**article, **fields}
        except Exception as exc:
            last_exc = exc
            if attempt < _cfg.PROCESSOR_MAX_RETRIES:
                time.sleep(_cfg.PROCESSOR_RETRY_DELAY)
    _write_failed(article, str(last_exc), run_id)
    return None


def triage_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Add relevance_score/relevance_reason to every article; dead-letter failures."""
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()
    score_cache = _load_score_cache()

    results = []
    with tqdm(total=len(articles), desc="Triage", unit="art", leave=False) as bar:
        for article in articles:
            result = _triage_one(article, client, use_local, run_id, score_cache)
            if result is not None:
                results.append(result)
            bar.update(1)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `just test-one tests/test_processor.py::TestTriageArticles tests/test_processor.py::TestTriageCache -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: add triage_articles with versioned relevance cache"
```

---

### Task 5: `score_articles()` with versioned impact/authenticity cache

**Files:**
- Modify: `src/processor.py` (add score functions)
- Test: `tests/test_processor.py` (new `TestScoreArticles`, `TestScoreCache`; delete old `TestScoreCache` that used `process_articles`)

**Interfaces:**
- Produces:
  - `_write_score_cache(url: str, fields: dict) -> None` — upserts the 4 impact/auth fields + `score_version`
  - `score_articles(articles: list[dict], run_id: str | None = None) -> list[dict]` — adds impact/auth fields to gate survivors; dead-letters failures; preserves triage fields
- Consumes: `_build_user_content`, `_call_local`, `_call_anthropic`, `SCORING_SYSTEM_PROMPT`, `_SCORE_SCHEMA`, `SCORE_VERSION`, `_load_score_cache`.

- [ ] **Step 1: Write the failing tests**

Delete the old `TestScoreCache` class (its tests call `process_articles`). Add:

```python
SCORE_RESPONSE = {
    "impact_score": 8, "authenticity_score": 7,
    "impact_reason": "Field-moving research.", "authenticity_reason": "Named lab source.",
}


def _triaged_article(url="https://example.com/s", relevance=9) -> dict:
    return {**make_article(url=url), "relevance_score": relevance, "relevance_reason": "on topic"}


class TestScoreArticles:
    def test_impact_and_auth_merged_preserving_triage(self):
        article = _triaged_article()
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch("anthropic.Anthropic", return_value=mock_client):
            results = score_articles([article])
        assert results[0]["impact_score"] == 8
        assert results[0]["authenticity_score"] == 7
        assert results[0]["relevance_score"] == 9  # triage field preserved

    def test_local_scoring_uses_512_max_tokens(self):
        article = _triaged_article()
        mock_resp = _make_local_mock_response(json.dumps(SCORE_RESPONSE))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            score_articles([article])
        assert mock_post.call_args[1]["json"]["max_tokens"] == 512

    def test_failure_dead_letters_and_excludes(self):
        article = _triaged_article()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("down")
        with patch.object(_cfg, "LLM_PROVIDER", "anthropic"), \
             patch.object(_cfg, "PROCESSOR_MAX_RETRIES", 0), \
             patch.object(_cfg, "PROCESSOR_RETRY_DELAY", 0.0), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.processor._write_failed") as mock_dlq:
            results = score_articles([article])
        assert len(results) == 0
        mock_dlq.assert_called_once()


class TestScoreCache:
    def test_cache_hit_skips_llm_when_version_matches(self, db):
        from src.processor import SCORE_VERSION
        url = "https://example.com/score-cached"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=8, authenticity_score=7,
                impact_reason="i", authenticity_reason="a", score_version=SCORE_VERSION,
            ))
        with patch("anthropic.Anthropic") as mock_cls, \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = score_articles([_triaged_article(url=url)])
        mock_cls.return_value.messages.create.assert_not_called()
        assert results[0]["impact_score"] == 8

    def test_stale_score_version_is_miss(self, db):
        url = "https://example.com/score-stale"
        with get_session() as session:
            session.add(ArticleScore(
                url=url, impact_score=2, authenticity_score=2,
                impact_reason="old", authenticity_reason="old", score_version="OLD000000000",
            ))
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            results = score_articles([_triaged_article(url=url)])
        mock_client.messages.create.assert_called_once()
        assert results[0]["impact_score"] == 8

    def test_cache_miss_writes_score_version(self, db):
        from src.processor import SCORE_VERSION
        url = "https://example.com/score-write"
        mock_client = _make_mock_client(json.dumps(SCORE_RESPONSE))
        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch.object(_cfg, "LLM_PROVIDER", "anthropic"):
            score_articles([_triaged_article(url=url)])
        with get_session() as session:
            row = session.get(ArticleScore, url)
        assert row.impact_score == 8
        assert row.score_version == SCORE_VERSION
```

Update the top-of-file import to include `score_articles`.

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestScoreArticles -v`
Expected: FAIL — `ImportError: cannot import name 'score_articles'`

- [ ] **Step 3: Add the score functions in `src/processor.py`**

```python
def _write_score_cache(url: str, fields: dict) -> None:
    """Upsert impact/authenticity fields + score_version. Best-effort: never raises."""
    try:
        values = {"url": url, "cached_at": func.now(), "score_version": SCORE_VERSION,
                  **{f: fields[f] for f in _SCORE_FIELDS}}
        stmt = pg_insert(ArticleScore).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ArticleScore.url],
            set_={"cached_at": func.now(), "score_version": SCORE_VERSION,
                  **{f: fields[f] for f in _SCORE_FIELDS}},
        )
        with get_session() as session:
            session.execute(stmt)
    except Exception:
        pass


def _score_one(article, client, use_local, run_id=None, score_cache=None) -> dict | None:
    url = article.get("url", "")
    if score_cache and url in score_cache:
        cached = score_cache[url]
        if cached.get("impact_score") is not None and cached.get("score_version") == SCORE_VERSION:
            return {**article, **{f: cached[f] for f in _SCORE_FIELDS}}

    user_content = _build_user_content(article, SCORE_DESC_CHARS)
    last_exc: Exception | None = None
    for attempt in range(_cfg.PROCESSOR_MAX_RETRIES + 1):
        try:
            text = (_call_local(SCORING_SYSTEM_PROMPT, user_content, 512, _SCORE_SCHEMA)
                    if use_local else _call_anthropic(client, SCORING_SYSTEM_PROMPT, user_content, 512))
            parsed = json.loads(text)
            fields = {f: parsed[f] for f in _SCORE_FIELDS}
            _write_score_cache(url, fields)
            return {**article, **fields}
        except Exception as exc:
            last_exc = exc
            if attempt < _cfg.PROCESSOR_MAX_RETRIES:
                time.sleep(_cfg.PROCESSOR_RETRY_DELAY)
    _write_failed(article, str(last_exc), run_id)
    return None


def score_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Add impact/authenticity scores to gate survivors; dead-letter failures."""
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()
    score_cache = _load_score_cache()

    results = []
    with tqdm(total=len(articles), desc="Scoring", unit="art", leave=False) as bar:
        for article in articles:
            result = _score_one(article, client, use_local, run_id, score_cache)
            if result is not None:
                results.append(result)
            bar.update(1)
    return results
```

- [ ] **Step 4: Run the full processor test file**

Run: `just test-one tests/test_processor.py -v`
Expected: PASS (triage, score, cache, summary, and the version-column tests). If any old test still references `process_articles` or the removed `SYSTEM_PROMPT`, delete/migrate it now — no test may import a removed symbol.

- [ ] **Step 5: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: add score_articles with versioned impact/authenticity cache"
```

---

### Task 5b: Raise the summary input cap to the full abstract

**Files:**
- Modify: `src/processor.py` (`summarize_articles` user-content construction)
- Test: `tests/test_processor.py` (extend `TestSummarizeArticles`)

**Interfaces:**
- Consumes: `_build_user_content` (Task 3), `SUMMARY_DESC_CHARS = 1900` (Task 3).

The summary call currently truncates the description at 500 chars via an inline
snippet. Same rationale as scoring: for arXiv (the only source over ~400 chars)
the summary reads better from the full abstract. Reuse `_build_user_content`
(the inline construction is byte-for-byte the same field layout) so there is
one code path.

- [ ] **Step 1: Write the failing test**

Add to `TestSummarizeArticles` in `tests/test_processor.py`:

```python
    def test_summary_input_uses_full_abstract_not_500(self):
        article = {**_make_processed_article(), "description": "z" * 2000}
        mock_resp = _make_local_mock_response(json.dumps({"summary": "ok"}))
        with patch.object(_cfg, "LLM_PROVIDER", "local"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            summarize_articles([article])
        sent = mock_post.call_args[1]["json"]["messages"][1]["content"]
        assert "z" * 1900 in sent      # full abstract, not truncated at 500
        assert "z" * 1901 not in sent  # still capped at SUMMARY_DESC_CHARS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_processor.py::TestSummarizeArticles::test_summary_input_uses_full_abstract_not_500 -v`
Expected: FAIL — only 500 `z`s reach the payload (`assert "z" * 1900 in sent`).

- [ ] **Step 3: Use `_build_user_content` with the summary cap**

In `summarize_articles` (`src/processor.py`), replace the inline snippet build:

```python
            # Cap description length to avoid overflowing local model context windows
            description_snippet = (article.get("description", "") or "")[:500]
            user_content = (
                f"Author: {article.get('author', '')}\n"
                f"Publication: {article.get('publication', '')}\n"
                f"Title: {article.get('title', '')}\n"
                f"Description: {description_snippet}"
            )
```

with:

```python
            user_content = _build_user_content(article, SUMMARY_DESC_CHARS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test-one tests/test_processor.py::TestSummarizeArticles -v`
Expected: PASS (new test plus the existing summary tests; the fallback-on-failure tests are unaffected — they assert the `_clean_description` fallback, not the prompt input).

- [ ] **Step 5: Commit**

```bash
git add src/processor.py tests/test_processor.py
git commit -m "feat: feed summary generation the full abstract (1900 chars)"
```

---

### Task 6: Wire two-stage flow into `main.py`

**Files:**
- Modify: `src/main.py:19` (import), `src/main.py:62-110` (stage orchestration + bar total)
- Test: `tests/test_main.py` (update all `process_articles` patches to `triage_articles` + `score_articles`; update tqdm total)

**Interfaces:**
- Consumes: `triage_articles`, `score_articles` (Tasks 4-5), `filter_articles`, `rank_articles`.
- Produces: pipeline order `fetch → triage → filter → score → rank → seen-mark → summarize → render → send`; stage bar `total=8`.

- [ ] **Step 1: Update the failing tests**

In `tests/test_main.py`:
- Change the import to `from src.main import run_pipeline` (already present) — no processor import needed.
- In every `with (...)` block, replace `patch("src.main.process_articles", …)` with **two** patches:
  `patch("src.main.triage_articles", side_effect=lambda articles, **kw: articles),`
  `patch("src.main.score_articles", side_effect=lambda articles, **kw: articles),`
  For tests that assert filter receives kept-only articles, note triage now runs before filter and score after — keep `filter_articles` returning `(kept, dropped)` as before.
- Update `test_run_pipeline_uses_tqdm`: change expected total from 7 to 8 and `bar.update.call_count` from 7 to 8:

```python
    tqdm_cls.assert_called_once_with(total=8, desc="Pipeline", leave=True)
    assert bar_mock.update.call_count == 8
```

- Update `TestFilterCalledBetweenProcessAndRank.test_filter_called_after_process_before_rank`: the order list should become `["triage", "filter", "score", "rank"]`:

```python
        def mock_triage(articles, run_id=None):
            call_order.append("triage")
            return articles
        def mock_filter(articles):
            call_order.append("filter")
            return kept, dropped
        def mock_score(articles, run_id=None):
            call_order.append("score")
            return articles
        def mock_rank(articles):
            call_order.append("rank")
            return articles
        with (
            patch("src.main.fetch_articles", side_effect=mock_fetch),
            patch("src.main.triage_articles", side_effect=mock_triage),
            patch("src.main.filter_articles", side_effect=mock_filter),
            patch("src.main.score_articles", side_effect=mock_score),
            patch("src.main.rank_articles", side_effect=mock_rank),
            patch("src.main.summarize_articles", side_effect=lambda articles, **kw: articles),
            patch("src.main.render_newsletter", return_value=("<html/>", "plain")),
            patch("src.main.send_newsletter"),
        ):
            from src.main import run_pipeline
            run_pipeline(run_id="test-order-run")
        assert call_order == ["triage", "filter", "score", "rank"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test-one tests/test_main.py -v`
Expected: FAIL — `AttributeError: <module 'src.main'> does not have the attribute 'triage_articles'`

- [ ] **Step 3: Rewire `src/main.py`**

Change the import (line 19):

```python
from src.processor import triage_articles, score_articles, summarize_articles
```

Replace the stage block. Change `with tqdm(total=7, …)` to `total=8`, and restructure so filter sits between triage and score:

```python
        with tqdm(total=8, desc="Pipeline", leave=True) as bar:
            bar.set_description("Fetching articles")
            articles = fetch_articles()
            tqdm.write(f"[{run_id}] Fetched {len(articles)} articles")
            bar.update(1)

            provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
            bar.set_description(f"Triaging relevance with {provider_label}")
            articles = triage_articles(articles, run_id=run_id)
            tqdm.write(f"[{run_id}] Triaged {len(articles)} articles")
            bar.update(1)

            bar.set_description("Filtering by relevance")
            articles, dropped_articles = filter_articles(articles)
            tqdm.write(f"[{run_id}] Kept {len(articles)} articles, dropped {len(dropped_articles)}")
            bar.update(1)

            if dropped_articles:
                with get_session() as session:
                    session.add_all([
                        FilteredArticle(
                            run_id=run_id,
                            url=a["url"],
                            title=a["title"],
                            publication=a["publication"],
                            relevance_score=a["relevance_score"],
                            relevance_reason=a.get("relevance_reason", ""),
                        )
                        for a in dropped_articles
                    ])

            bar.set_description("Scoring impact & authenticity")
            articles = score_articles(articles, run_id=run_id)
            tqdm.write(f"[{run_id}] Scored {len(articles)} articles")
            bar.update(1)

            bar.set_description("Ranking")
            articles = rank_articles(articles)
            tqdm.write(f"[{run_id}] Ranked {len(articles)} articles")
            bar.update(1)

            now_iso = datetime.now(timezone.utc).isoformat()
            seen_rows = [{"url": a["url"], "seen_at": now_iso} for a in articles[:TOP_N]]
            if seen_rows:
                stmt = pg_insert(SeenArticle).values(seen_rows).on_conflict_do_nothing(
                    index_elements=[SeenArticle.url]
                )
                with get_session() as session:
                    session.execute(stmt)

            bar.set_description("Generating summaries")
            articles = summarize_articles(articles, run_id=run_id)
            tqdm.write(f"[{run_id}] Summaries generated for {len(articles)} articles")
            bar.update(1)

            with get_session() as session:
                session.add_all([
                    RunArticle(
                        run_id=run_id,
                        title=a["title"],
                        url=a["url"],
                        publication=a["publication"],
                        published_at=str(a.get("published_at", "")),
                        rank_score=a.get("rank_score"),
                    )
                    for a in articles
                ])

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
                send_newsletter(html, plain_text, started_at, run_id=run_id)
                tqdm.write(f"[{run_id}] Email sent")
            bar.update(1)
```

(That is 8 `bar.update(1)` calls: fetch, triage, filter, score, rank, summaries, render, send.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `just test-one tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: wire fetch->triage->filter->score->rank two-stage pipeline"
```

---

### Task 7: Fold relevance into the ranking formula

**Files:**
- Modify: `src/ranker.py:4-18`, `src/config.py:84-86` (schema doc block)
- Test: `tests/test_ranker.py` (update `make_article`, `expected_rank`)

**Interfaces:**
- Produces: `rank_score = impact*0.5 + relevance*0.3 + authenticity*0.2`.
- Consumes: articles carry `relevance_score` (from triage) by the time they reach `rank_articles`.

- [ ] **Step 1: Update the failing tests**

In `tests/test_ranker.py`, update the helper and expected formula:

```python
def make_article(url: str, impact: float, authenticity: float, publication: str = "example.com",
                 relevance: float = 8.0) -> dict:
    return {"url": url, "impact_score": impact, "authenticity_score": authenticity,
            "relevance_score": relevance, "publication": publication}


def expected_rank(impact: float, authenticity: float, relevance: float = 8.0) -> float:
    return impact * 0.5 + relevance * 0.3 + authenticity * 0.2
```

Update the three `TestRankScoreComputation` assertions that pass explicit values so they include relevance. For `test_rank_score_formula_various`, pass matching `relevance` into both `make_article` and `expected_rank`, e.g.:

```python
    def test_rank_score_formula_various(self):
        articles = [
            make_article("http://a.com", 10.0, 10.0, relevance=10.0),
            make_article("http://b.com", 1.0, 1.0, relevance=1.0),
            make_article("http://c.com", 5.0, 7.5, relevance=6.0),
        ]
        result = rank_articles(articles)
        scores = {a["url"]: a["rank_score"] for a in result}
        assert scores["http://a.com"] == pytest.approx(expected_rank(10.0, 10.0, 10.0))
        assert scores["http://b.com"] == pytest.approx(expected_rank(1.0, 1.0, 1.0))
        assert scores["http://c.com"] == pytest.approx(expected_rank(5.0, 7.5, 6.0))
```

Add one test asserting relevance moves rank:

```python
    def test_relevance_breaks_impact_auth_tie(self):
        articles = [
            make_article("http://core.com", 7.0, 7.0, relevance=10.0),
            make_article("http://adjacent.com", 7.0, 7.0, relevance=6.0),
        ]
        result = rank_articles(articles)
        assert result[0]["url"] == "http://core.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test-one tests/test_ranker.py -v`
Expected: FAIL — assertion mismatch on `rank_score` (old 0.6/0.4 formula) and `KeyError`-free but wrong ordering on the new tie test.

- [ ] **Step 3: Update the formula in `src/ranker.py`**

Replace the docstring formula line and the computation:

```python
def rank_articles(articles: list[dict]) -> list[dict]:
    """Rank articles by weighted score and return top TOP_N with source diversity.

    rank_score = (impact_score * 0.5) + (relevance_score * 0.3) + (authenticity_score * 0.2)

    Articles are sorted descending by rank_score, ties broken by url ascending.
    At most MAX_PER_NEWSLETTER_SOURCE articles from any single publication are
    included for source diversity. Only the top TOP_N articles are returned.
    """
    scored = []
    for article in articles:
        rank_score = (
            article["impact_score"] * 0.5
            + article.get("relevance_score", 0) * 0.3
            + article["authenticity_score"] * 0.2
        )
        scored.append({**article, "rank_score": rank_score})
```

- [ ] **Step 4: Update the config schema doc block**

In `src/config.py`, update the ranker line in the schema comment (currently line ~85):

```python
#   rank_score:          float — (impact_score * 0.5) + (relevance_score * 0.3) + (authenticity_score * 0.2)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `just test-one tests/test_ranker.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ranker.py src/config.py tests/test_ranker.py
git commit -m "feat: weight relevance into rank_score (impact .5 / relevance .3 / auth .2)"
```

---

### Task 8: Golden-set eval harness (created, NOT executed)

**Files:**
- Create: `eval/golden_articles.json`, `scripts/eval_prompts.py`
- Modify: `justfile` (add `eval` recipe)
- Test: `tests/test_eval.py`

**Interfaces:**
- Produces: `scripts.eval_prompts.score_against_golden(golden: list[dict], scored: list[dict]) -> dict` returning `{"relevance_mae": float, "impact_mae": float, "authenticity_mae": float, "gate_agreement": float}`.
- Consumes: `triage_articles`, `score_articles`, `filter_articles`.

**Global-constraint reminder:** the `just eval` recipe hits the LLM. Do NOT run it while the in-flight pipeline holds `:8089`. Only the pure-logic `test_eval.py` runs in this task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval.py`:

```python
from scripts.eval_prompts import score_against_golden


def test_mae_and_gate_agreement_perfect_match():
    golden = [
        {"url": "u1", "relevance_score": 9, "impact_score": 8, "authenticity_score": 7},
        {"url": "u2", "relevance_score": 3, "impact_score": 4, "authenticity_score": 5},
    ]
    scored = [dict(g) for g in golden]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["relevance_mae"] == 0.0
    assert result["impact_mae"] == 0.0
    assert result["authenticity_mae"] == 0.0
    assert result["gate_agreement"] == 1.0


def test_mae_computes_absolute_error():
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 8, "authenticity_score": 8}]
    scored = [{"url": "u1", "relevance_score": 6, "impact_score": 10, "authenticity_score": 8}]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["relevance_mae"] == 2.0
    assert result["impact_mae"] == 2.0
    assert result["authenticity_mae"] == 0.0


def test_gate_agreement_counts_threshold_side():
    # golden relevance 8 (pass), scored 4 (fail) -> disagreement
    golden = [{"url": "u1", "relevance_score": 8, "impact_score": 5, "authenticity_score": 5}]
    scored = [{"url": "u1", "relevance_score": 4, "impact_score": 5, "authenticity_score": 5}]
    result = score_against_golden(golden, scored, threshold=6)
    assert result["gate_agreement"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `just test-one tests/test_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval_prompts'`

- [ ] **Step 3: Create the eval scoring module**

Create `scripts/eval_prompts.py`:

```python
"""Golden-set evaluation for scoring prompts.

`score_against_golden` is pure logic (unit-tested). `main()` runs the live
pipeline stages against eval/golden_articles.json and prints agreement metrics
— it hits the configured LLM, so only run it when the llama.cpp server is free.
"""
import argparse
import json
from pathlib import Path

_GOLDEN_PATH = Path(__file__).parent.parent / "eval" / "golden_articles.json"


def score_against_golden(golden: list[dict], scored: list[dict], threshold: int = 6) -> dict:
    """Compare model scores against hand-labeled golden scores.

    Returns mean absolute error per dimension and gate-decision agreement
    (fraction of articles where model and golden agree on relevance >= threshold).
    """
    by_url = {s["url"]: s for s in scored}
    dims = ("relevance_score", "impact_score", "authenticity_score")
    errors = {d: [] for d in dims}
    gate_hits = 0
    n = 0
    for g in golden:
        s = by_url.get(g["url"])
        if s is None:
            continue
        n += 1
        for d in dims:
            if d in g and g[d] is not None and s.get(d) is not None:
                errors[d].append(abs(g[d] - s[d]))
        if (g["relevance_score"] >= threshold) == (s.get("relevance_score", 0) >= threshold):
            gate_hits += 1

    def _mae(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "relevance_mae": _mae(errors["relevance_score"]),
        "impact_mae": _mae(errors["impact_score"]),
        "authenticity_mae": _mae(errors["authenticity_score"]),
        "gate_agreement": gate_hits / n if n else 0.0,
    }


def main() -> None:
    from src import config as _cfg
    from src.processor import triage_articles, score_articles

    parser = argparse.ArgumentParser(description="Evaluate scoring prompts against the golden set")
    parser.add_argument("--threshold", type=int, default=_cfg.RELEVANCE_THRESHOLD)
    args = parser.parse_args()

    golden = json.loads(_GOLDEN_PATH.read_text())
    # Run the real two-stage scoring on the golden articles.
    triaged = triage_articles([{k: a[k] for k in ("url", "title", "description", "author", "publication")} for a in golden])
    scored = score_articles(triaged)
    metrics = score_against_golden(golden, scored, threshold=args.threshold)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create the golden fixture**

Create `eval/golden_articles.json` with ~15-20 real recent articles. Seed it with a small starter set now (the implementer/user expands it later by pulling from `run_articles`/`filtered_articles`). Minimum viable content:

```json
[
  {
    "url": "https://arxiv.org/abs/2506.00001",
    "title": "Scaling Agentic Reasoning with Tool-Use Curricula",
    "description": "A new training curriculum improves multi-step tool use in LLM agents...",
    "author": "A. Researcher",
    "publication": "arxiv.org",
    "relevance_score": 9,
    "impact_score": 7,
    "authenticity_score": 9
  },
  {
    "url": "https://techcrunch.com/2026/06/30/startup-raises-series-a",
    "title": "AI note-taking startup raises $12M Series A",
    "description": "The company plans to expand its sales team...",
    "author": "Staff",
    "publication": "techcrunch.com",
    "relevance_score": 4,
    "impact_score": 3,
    "authenticity_score": 6
  }
]
```

Add a comment in the design/PR that this file should grow to ~15-20 entries before the eval numbers are meaningful.

- [ ] **Step 5: Add the `just eval` recipe**

In `justfile`, under the App section:

```
# Evaluate scoring prompts against eval/golden_articles.json (HITS THE LLM —
# only run when the llama.cpp server is free). Reports MAE + gate agreement.
eval:
    poetry run python -m scripts.eval_prompts
```

- [ ] **Step 6: Run the unit test to verify it passes**

Run: `just test-one tests/test_eval.py -v`
Expected: PASS. (Do NOT run `just eval`.)

- [ ] **Step 7: Commit**

```bash
git add eval/golden_articles.json scripts/eval_prompts.py justfile tests/test_eval.py
git commit -m "feat: add golden-set eval harness for scoring prompts (not run)"
```

---

### Task 9: Full-suite verification and docs

**Files:**
- Modify: `CLAUDE.md` (processor description), `.env.example` (if any new var — none expected)
- Test: full suite

- [ ] **Step 1: Run the entire test suite**

Run: `just test`
Expected: PASS. Investigate any failure referencing `process_articles`, `SYSTEM_PROMPT`, the old rank formula, or `total=7` — those are leftovers to migrate.

- [ ] **Step 2: Update `CLAUDE.md` architecture notes**

In the `src/processor.py` bullet under "Module responsibilities", replace the single-call description with the two-stage shape:

```
- **`src/processor.py`** — Two-stage LLM scoring. `triage_articles()` makes one cheap relevance-only call per fetched article; after the relevance gate (`filter.py`), `score_articles()` makes one impact+authenticity call per survivor. Local calls use grammar-constrained JSON (`response_format`). Descriptions are truncated per stage — 500 chars for triage, 1,900 (≈ a full arXiv abstract) for scoring, since only arXiv descriptions exceed ~400 chars. Scores cache in `article_scores` keyed by URL, versioned per stage (`triage_version`/`score_version`) so a prompt change invalidates stale rows. Failures dead-letter to `failed_articles` and are excluded.
```

Also update the `src/ranker.py` bullet's formula to `impact*0.5 + relevance*0.3 + authenticity*0.2`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe two-stage scoring and new rank formula"
```

- [ ] **Step 4 (deferred, user-gated): run the live eval once the server is free**

When the in-flight pipeline finishes and the user confirms `:8089` is free (and `LLM_PROVIDER=local`), run `just eval` and record the MAE / gate-agreement numbers. Optionally run `just run-clean` for a cold-cache end-to-end timing check to confirm the expected ~2.5-3.5× speedup. This step is NOT part of the automated implementation — it requires the user's go-ahead.

---

## Self-Review

**Spec coverage:**
- Two-stage restructure → Tasks 4, 5, 6 ✓
- Call tightening (500-char truncation, `response_format`, max_tokens) → Task 3 (helpers), applied in Tasks 4/5 ✓
- Rubric redesign (anchored impact/auth, calibration line) → Task 2 ✓
- Ranking formula → Task 7 ✓
- Cache versioning (two columns, per-stage) → Tasks 1, 4, 5 ✓
- Golden-set eval → Task 8 (created, not run) ✓
- Error handling unchanged, testing conventions → all tasks, verified Task 9 ✓
- "No LLM/eval while run holds the server" constraint → Global Constraints + Task 8/9 gating ✓

**Placeholder scan:** No TBD/TODO; all code steps show full code. Golden fixture ships a real starter set with an explicit note to expand.

**Type consistency:** `triage_articles`/`score_articles` signatures `(articles, run_id=None) -> list[dict]` consistent across Tasks 4-6. `_TRIAGE_FIELDS`/`_SCORE_FIELDS`/`_ALL_CACHE_FIELDS` used consistently. `_call_local(system, user, max_tokens, schema)` / `_call_anthropic(client, system, user, max_tokens)` signatures consistent across Tasks 3-5. `score_against_golden(golden, scored, threshold=6)` consistent between Task 8 test and impl. Stage bar `total=8` consistent between main.py and test_main.py.

**Note on test churn:** Tasks 4-5 delete/migrate the `test_processor.py` classes that exercised the removed `process_articles`; Task 5 Step 4 and Task 9 Step 1 are the safety nets that catch any straggler importing a removed symbol.
