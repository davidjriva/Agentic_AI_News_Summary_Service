"""Article processor: calls an LLM to add summaries and scores to each article.

Supports two providers, selected via the LLM_PROVIDER config value:
  "anthropic" (default) — Anthropic SDK with prompt caching
  "local"               — local llama.cpp server via its OpenAI-compatible API

Articles that fail LLM processing after all retries are written to the
failed_articles dead-letter queue and excluded from results.
"""

import json
import time

import anthropic
import requests

from src import config as _cfg
from src.db import get_connection

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


def process_articles(articles: list[dict], run_id: str | None = None) -> list[dict]:
    """Process articles by calling the configured LLM to add summary and scores.

    Articles that fail after all retries are written to failed_articles and excluded.
    """
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    results = []
    for article in articles:
        result = _process_one(article, client, use_local, run_id)
        if result is not None:
            results.append(result)
    return results
