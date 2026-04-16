from __future__ import annotations

import logging
from typing import List

import anthropic

from config import Config
from .models import Article

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert editorial assistant for a technology newsletter written in the style "
    "of The New York Times. Your summaries are factual, concise, written in third person, "
    "and use past tense for completed events. You never editorialize or speculate beyond "
    "what is stated in the source material."
)

_INTRO_SYSTEM_PROMPT = (
    "You are a senior editor writing introductory copy for a technology newsletter. "
    "Your introductions are engaging, authoritative, and highlight the key themes of the "
    "edition in an editorial voice. Keep it to exactly two sentences."
)


class NewsletterSummarizer:
    def __init__(self, config: Config) -> None:
        self._config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def summarize_article(self, article: Article) -> Article:
        prompt = (
            f"Article title: {article.title}\n\n"
            f"Article content:\n{article.raw_content[:4000]}\n\n"
            "Summarize this article in 2-3 sentences. Extract the key insight. "
            "Keep it factual and concise. Write in NYT style: third person, past tense for events."
        )

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            summary = next(
                (b.text for b in response.content if b.type == "text"), ""
            ).strip()
            article.summary = summary if summary else article.title
        except anthropic.APIError:
            logger.warning(
                "Anthropic API error summarizing '%s', using title as fallback",
                article.title,
                exc_info=True,
            )
            article.summary = article.title

        return article

    def summarize_articles(self, articles: List[Article]) -> List[Article]:
        total = len(articles)
        summarized: List[Article] = []
        for idx, article in enumerate(articles, start=1):
            logger.info("Summarizing article %d/%d: %s", idx, total, article.title)
            summarized.append(self.summarize_article(article))
        return summarized

    def generate_newsletter_intro(
        self, newsletter_title: str, article_titles: List[str]
    ) -> str:
        titles_block = "\n".join(f"- {t}" for t in article_titles[:15])
        prompt = (
            f"Newsletter name: {newsletter_title}\n\n"
            f"Articles in this edition:\n{titles_block}\n\n"
            "Write a two-sentence newsletter introduction that mentions the key themes "
            "across these articles in an engaging editorial voice."
        )

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=128,
                system=[
                    {
                        "type": "text",
                        "text": _INTRO_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            intro = next(
                (b.text for b in response.content if b.type == "text"), ""
            ).strip()
            return intro if intro else f"Welcome to {newsletter_title}."
        except anthropic.APIError:
            logger.warning(
                "Anthropic API error generating newsletter intro", exc_info=True
            )
            return f"Welcome to {newsletter_title}."
