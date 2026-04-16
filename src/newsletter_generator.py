from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import Config
from .models import Article, Newsletter, NewsletterSection

logger = logging.getLogger(__name__)

_AI_KEYWORDS = {
    "artificial intelligence", "machine learning", "deep learning", "neural network",
    "large language model", "llm", "gpt", "openai", "anthropic", "deepmind",
    "generative ai", "ai safety", "chatbot", "transformer", "robotics",
    "computer vision", "natural language", "reinforcement learning",
}

_BUSINESS_KEYWORDS = {
    "startup", "funding", "venture capital", "acquisition", "ipo", "revenue",
    "product launch", "partnership", "ceo", "company", "enterprise", "billion",
    "million", "market", "investment", "raises", "valuation",
}

_ISSUE_COUNTER_PATH = (
    Path(__file__).parent.parent / "data" / "issue_counter.json"
)


class NewsletterGenerator:
    def __init__(self, config: Config) -> None:
        self._config = config
        templates_dir = Path(__file__).parent.parent / "templates"
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    # ── Public API ──────────────────────────────────────────────────────────────

    def build_newsletter(self, articles: List[Article]) -> Newsletter:
        issue_number = self._next_issue_number()

        featured_articles = articles[:3]
        remaining = articles[3:]

        ai_articles: List[Article] = []
        business_articles: List[Article] = []
        brief_articles: List[Article] = []

        for article in remaining:
            haystack = (article.title + " " + article.raw_content).lower()
            if any(kw in haystack for kw in _AI_KEYWORDS):
                ai_articles.append(article)
            elif any(kw in haystack for kw in _BUSINESS_KEYWORDS):
                business_articles.append(article)
            else:
                brief_articles.append(article)

        sections: List[NewsletterSection] = []

        if featured_articles:
            sections.append(
                NewsletterSection(
                    title="Top Stories",
                    articles=featured_articles,
                    section_type="featured",
                )
            )
        if ai_articles:
            sections.append(
                NewsletterSection(
                    title="AI & Machine Learning",
                    articles=ai_articles[: self._config.max_articles_per_section],
                    section_type="standard",
                )
            )
        if business_articles:
            sections.append(
                NewsletterSection(
                    title="Industry & Business",
                    articles=business_articles[: self._config.max_articles_per_section],
                    section_type="standard",
                )
            )
        if brief_articles:
            sections.append(
                NewsletterSection(
                    title="In Brief",
                    articles=brief_articles[:5],
                    section_type="brief",
                )
            )

        return Newsletter(
            title=self._config.newsletter_title,
            tagline=self._config.newsletter_tagline,
            date=datetime.now(),
            sections=sections,
            issue_number=issue_number,
        )

    def render_html(self, newsletter: Newsletter) -> str:
        try:
            template = self._jinja_env.get_template("newsletter.html")
            return template.render(newsletter=newsletter)
        except Exception:
            logger.error("Failed to render HTML template", exc_info=True)
            raise

    def render_plaintext(self, newsletter: Newsletter) -> str:
        lines: List[str] = [
            f"{newsletter.title}",
            f"Issue #{newsletter.issue_number} | {newsletter.date_display}",
            newsletter.tagline,
            "=" * 60,
            "",
        ]

        for section in newsletter.sections:
            lines.append(section.title.upper())
            lines.append("-" * len(section.title))
            lines.append("")

            for article in section.articles:
                lines.append(f"* {article.title}")
                if article.summary and section.section_type != "brief":
                    lines.append(f"  {article.summary}")
                lines.append(f"  {article.url}")
                lines.append("")

            lines.append("")

        lines.append("─" * 60)
        lines.append(f"You received this because you subscribed to {newsletter.title}.")

        return "\n".join(lines)

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _next_issue_number(self) -> int:
        _ISSUE_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)

        if _ISSUE_COUNTER_PATH.exists():
            try:
                data = json.loads(_ISSUE_COUNTER_PATH.read_text())
                current = int(data.get("issue_number", 0))
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Could not parse %s, resetting counter", _ISSUE_COUNTER_PATH
                )
                current = 0
        else:
            current = 0

        next_issue = current + 1
        _ISSUE_COUNTER_PATH.write_text(
            json.dumps({"issue_number": next_issue}, indent=2)
        )
        return next_issue
