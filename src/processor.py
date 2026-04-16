"""Article processor: calls an LLM to add summaries and scores to each article.

Supports two providers, selected via the LLM_PROVIDER config value:
  "anthropic" (default) — Anthropic SDK with prompt caching
  "local"               — local llama.cpp server via its OpenAI-compatible API
"""

import json

import anthropic
import requests

from src import config as _cfg

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
            "max_tokens": 512,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def process_articles(articles: list[dict]) -> list[dict]:
    """Process articles by calling the configured LLM to add summary and scores."""
    use_local = _cfg.LLM_PROVIDER == "local"
    client = None if use_local else anthropic.Anthropic()

    results = []
    for article in articles:
        user_content = (
            f"Author: {article['author']}\n"
            f"Publication: {article['publication']}\n"
            f"Title: {article['title']}\n"
            f"Description: {article['description']}"
        )

        try:
            if use_local:
                response_text = _call_local_llm(user_content)
            else:
                response_text = _call_anthropic(client, user_content)

            parsed = json.loads(response_text)

            article = {
                **article,
                "summary": parsed["summary"],
                "impact_score": parsed["impact_score"],
                "authenticity_score": parsed["authenticity_score"],
                "relevance_score": parsed["relevance_score"],
                "impact_reason": parsed["impact_reason"],
                "authenticity_reason": parsed["authenticity_reason"],
                "relevance_reason": parsed["relevance_reason"],
            }
        except Exception:
            article = {
                **article,
                "impact_score": 5,
                "authenticity_score": 5,
                "relevance_score": 5,
                "summary": article["description"][:200],
                "impact_reason": "",
                "authenticity_reason": "",
                "relevance_reason": "",
            }

        results.append(article)

    return results
