"""Article processor: calls an LLM to add summaries and scores to each article.

Supports two providers, selected via the LLM_PROVIDER config value:
  "anthropic" (default) — Anthropic SDK with prompt caching
  "local"               — local llama.cpp server via its OpenAI-compatible API

Articles that fail LLM processing after all retries are written to the
failed_articles dead-letter queue and excluded from results.
"""

import json
import re
import time

import anthropic
import requests
from tqdm import tqdm

from src import config as _cfg
from src.db import get_connection

SYSTEM_PROMPT = """You are an AI news analyst specializing in agentic AI, machine learning, and deep learning. For each article provided, return a JSON object with exactly these keys:
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

SUMMARY_SYSTEM_PROMPT = """You are an AI news journalist writing for a technical audience. Write a detailed editorial paragraph summarizing the provided article.

Cover all of the following in 4-6 sentences:
- Who: the organization, researchers, or individuals involved
- What: what was released, discovered, or announced — with concrete specifics
- When / Where: timing and context of origin
- Why it matters: concrete significance and implications for the AI/ML field

Return ONLY a valid JSON object with one key:
- summary: the full paragraph (continuous prose, no line breaks, no bullet points)"""


def _call_anthropic(client: anthropic.Anthropic, user_content: str) -> str:
    """Call the Anthropic API with prompt caching on the system message."""
    response = client.messages.create(
        model=_cfg.CLAUDE_MODEL,
        max_tokens=1024,
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
            "max_tokens": 1024,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


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


def _write_failed(article: dict, reason: str, run_id: str | None) -> None:
    """Persist a failed article to the dead-letter queue. Best-effort: never raises."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO failed_articles (run_id, url, title, publication, reason) VALUES (?, ?, ?, ?, ?)",
                (run_id, article.get("url", ""), article.get("title", ""), article.get("publication", ""), reason),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # best-effort: a DB failure must not abort the rest of the pipeline


def _load_score_cache(conn) -> dict[str, dict]:
    """Load all non-expired score cache entries keyed by URL. Caller owns the connection."""
    ttl_modifier = f"-{_cfg.SCORE_CACHE_TTL_DAYS} days"
    rows = conn.execute(
        "SELECT url, impact_score, authenticity_score, relevance_score, "
        "impact_reason, authenticity_reason, relevance_reason "
        "FROM article_scores "
        "WHERE cached_at >= datetime('now', ?)",
        (ttl_modifier,),
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


def summarize_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Re-generate full-paragraph summaries for the given articles.

    Best-effort: on failure, the original short summary is kept and the article
    is not dead-lettered — summary regeneration must never drop an article.
    """
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    results = []
    with tqdm(total=len(articles), desc="Summaries", unit="art", leave=False) as bar:
        for article in articles:
            # Cap description length to avoid overflowing local model context windows
            description_snippet = (article.get("description", "") or "")[:500]
            user_content = (
                f"Author: {article.get('author', '')}\n"
                f"Publication: {article.get('publication', '')}\n"
                f"Title: {article.get('title', '')}\n"
                f"Description: {description_snippet}"
            )
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
