import json

import anthropic

from src.config import CLAUDE_MODEL

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


def process_articles(articles: list[dict]) -> list[dict]:
    """Process articles by calling Claude API to add summary and scores."""
    client = anthropic.Anthropic()
    results = []

    for article in articles:
        user_content = (
            f"Author: {article['author']}\n"
            f"Publication: {article['publication']}\n"
            f"Title: {article['title']}\n"
            f"Description: {article['description']}"
        )

        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
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

            response_text = response.content[0].text
            parsed = json.loads(response_text)

            article = {
                **article,
                "summary": parsed["summary"],
                "impact_score": parsed["impact_score"],
                "authenticity_score": parsed["authenticity_score"],
                "impact_reason": parsed["impact_reason"],
                "authenticity_reason": parsed["authenticity_reason"],
            }
        except Exception:
            article = {
                **article,
                "impact_score": 5,
                "authenticity_score": 5,
                "summary": article["description"][:200],
                "impact_reason": "",
                "authenticity_reason": "",
            }

        results.append(article)

    return results
