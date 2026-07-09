"""Article processor: calls an LLM to add summaries and scores to each article.

Supports two providers, selected via the LLM_PROVIDER config value:
  "anthropic" (default) — Anthropic SDK with prompt caching
  "local"               — local llama.cpp server via its OpenAI-compatible API

Articles that fail LLM processing after all retries are written to the
failed_articles dead-letter queue and excluded from results.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone

import anthropic
import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tqdm import tqdm

from src import config as _cfg
from src.db import get_session
from src.models import ArticleScore, FailedArticle

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

SUMMARY_SYSTEM_PROMPT = """You are an AI news journalist writing for a technical audience. Write a detailed editorial paragraph summarizing the provided article.

Cover all of the following in 4-6 sentences:
- Who: the organization, researchers, or individuals involved
- What: what was released, discovered, or announced — with concrete specifics
- When / Where: timing and context of origin
- Why it matters: concrete significance and implications for the AI/ML field

Return ONLY a valid JSON object with one key:
- summary: the full paragraph (continuous prose, no line breaks, no bullet points)"""

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


def _call_anthropic_summary(client: anthropic.Anthropic, user_content: str) -> str:
    """Call the Anthropic API with the detailed paragraph summary prompt."""
    response = client.messages.create(
        model=_cfg.CLAUDE_MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text


def _call_local_llm_summary(user_content: str) -> str:
    """Call a local llama.cpp server with the detailed paragraph summary prompt."""
    response = requests.post(
        f"{_cfg.LOCAL_LLM_URL}/v1/chat/completions",
        json={
            "model": _cfg.LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 512,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _clean_description(desc: str, max_chars: int = 400) -> str:
    """Normalize a raw feed description for use as a summary fallback."""
    # Strip arXiv abstract preamble
    desc = re.sub(r"^arXiv:\S+\s+Announce Type:\s*\w+\s*Abstract:\s*", "", desc)
    # Strip HTML tags
    desc = re.sub(r"<[^>]+>", "", desc)
    # Collapse whitespace
    desc = " ".join(desc.split())
    if len(desc) > max_chars:
        desc = desc[:max_chars].rsplit(" ", 1)[0] + "\u2026"
    return desc


_TRIAGE_FIELDS = ("relevance_score", "relevance_reason")
_SCORE_FIELDS = ("impact_score", "authenticity_score", "impact_reason", "authenticity_reason")
_ALL_CACHE_FIELDS = _TRIAGE_FIELDS + _SCORE_FIELDS + ("triage_version", "score_version")


def _write_failed(article: dict, reason: str, run_id: str | None) -> None:
    """Persist a failed article to the dead-letter queue. Best-effort: never raises."""
    try:
        with get_session() as session:
            session.add(
                FailedArticle(
                    run_id=run_id,
                    url=article.get("url", ""),
                    title=article.get("title", ""),
                    publication=article.get("publication", ""),
                    reason=reason,
                )
            )
    except Exception:
        pass  # best-effort: a DB failure must not abort the rest of the pipeline


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


def summarize_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Re-generate full-paragraph summaries for the given articles.

    Best-effort: on failure, the cleaned RSS description is used as the summary
    and the article is not dead-lettered — summary regeneration must never drop an article.
    """
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    results = []
    with tqdm(total=len(articles), desc="Summaries", unit="art", leave=False) as bar:
        for article in articles:
            user_content = _build_user_content(article, SUMMARY_DESC_CHARS)
            try:
                for attempt in range(_cfg.PROCESSOR_MAX_RETRIES + 1):
                    try:
                        text = (
                            _call_local_llm_summary(user_content)
                            if use_local
                            else _call_anthropic_summary(client, user_content)
                        )
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
                    except Exception:
                        if attempt < _cfg.PROCESSOR_MAX_RETRIES:
                            time.sleep(_cfg.PROCESSOR_RETRY_DELAY)
                        else:
                            raise
            except Exception as exc:
                tqdm.write(f"[{run_id}] Summary regeneration failed for {article.get('url', '')}: {exc}")
                article = {**article, "summary": _clean_description(article.get("description", "") or "")}
            results.append(article)
            bar.update(1)
    return results
