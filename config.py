from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _parse_list(value: str | None, fallback: List[str]) -> List[str]:
    """Split a comma-separated env var into a stripped list, or return fallback."""
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Config:
    # ── Anthropic ──────────────────────────────────────────────────────────────
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )

    # ── RSS sources ────────────────────────────────────────────────────────────
    DEFAULT_FEEDS: List[str] = field(default_factory=lambda: [
        "https://feeds.feedburner.com/TechCrunch",       # TechCrunch
        "https://www.theverge.com/rss/index.xml",        # The Verge
        "https://venturebeat.com/feed/",                  # VentureBeat
        "https://www.wired.com/feed/rss",                # Wired
        "https://feeds.arstechnica.com/arstechnica/index",  # Ars Technica
    ])

    news_sources: List[str] = field(default_factory=list)

    # ── Topic filter ───────────────────────────────────────────────────────────
    TOPICS: List[str] = field(default_factory=lambda: [
        "artificial intelligence",
        "machine learning",
        "OpenAI",
        "Anthropic",
        "Google DeepMind",
        "large language model",
        "generative AI",
        "AI safety",
        "robotics",
        "neural network",
    ])

    # ── SMTP ───────────────────────────────────────────────────────────────────
    smtp_host: str = field(
        default_factory=lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com")
    )
    smtp_port: int = field(
        default_factory=lambda: int(os.environ.get("SMTP_PORT", "587"))
    )
    smtp_user: str = field(
        default_factory=lambda: os.environ.get("SMTP_USER", "")
    )
    smtp_password: str = field(
        default_factory=lambda: os.environ.get("SMTP_PASSWORD", "")
    )
    smtp_from: str = field(
        default_factory=lambda: os.environ.get("SMTP_FROM", "")
    )

    # ── Recipients ─────────────────────────────────────────────────────────────
    recipient_emails: List[str] = field(default_factory=list)

    # ── Newsletter metadata ────────────────────────────────────────────────────
    newsletter_title: str = field(
        default_factory=lambda: os.environ.get(
            "NEWSLETTER_TITLE", "AI & Tech Weekly Briefing"
        )
    )
    newsletter_tagline: str = field(
        default_factory=lambda: os.environ.get(
            "NEWSLETTER_TAGLINE",
            "Your curated digest of artificial intelligence and technology news",
        )
    )
    max_articles_per_section: int = field(
        default_factory=lambda: int(os.environ.get("MAX_ARTICLES_PER_SECTION", "5"))
    )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    send_time: str = field(
        default_factory=lambda: os.environ.get("SEND_TIME", "08:00")
    )

    def __post_init__(self) -> None:
        # Resolve list fields from env vars if they were not injected directly.
        if not self.news_sources:
            self.news_sources = _parse_list(
                os.environ.get("NEWS_SOURCES"), self.DEFAULT_FEEDS
            )

        if not self.recipient_emails:
            self.recipient_emails = _parse_list(
                os.environ.get("RECIPIENT_EMAILS"), []
            )

        # Strip surrounding quotes that some shells / .env parsers leave on values.
        self.newsletter_title = self.newsletter_title.strip('"').strip("'")
        self.newsletter_tagline = self.newsletter_tagline.strip('"').strip("'")

    def validate(self) -> None:
        """Raise ValueError for any configuration that would prevent the service from running."""
        errors: List[str] = []

        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set")
        if not self.news_sources:
            errors.append("NEWS_SOURCES must contain at least one RSS feed URL")

        # SMTP validation is only required when email sending is intended.
        email_sending_intended = bool(self.recipient_emails or self.smtp_user)
        if email_sending_intended:
            if not self.smtp_user:
                errors.append("SMTP_USER is not set (required when recipients are configured)")
            if not self.smtp_password:
                errors.append("SMTP_PASSWORD is not set (required when recipients are configured)")
            if not self.recipient_emails:
                errors.append("RECIPIENT_EMAILS must contain at least one address")

        if errors:
            raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))


# Module-level singleton — import and use directly, or call Config() for a fresh copy.
config = Config()
