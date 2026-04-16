from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from config import Config
from .models import Article

logger = logging.getLogger(__name__)


class NewsFetcher:
    def __init__(self, config: Config) -> None:
        self._config = config

    def fetch_all_feeds(self) -> List[Article]:
        all_articles: List[Article] = []
        seen_urls: set[str] = set()

        for feed_url in self._config.news_sources:
            try:
                feed = feedparser.parse(feed_url)
                source_name = feed.feed.get("title", feed_url)
                logger.info("Fetched %d entries from %s", len(feed.entries), source_name)

                for entry in feed.entries:
                    article = self._parse_feed_entry(entry, source_name)
                    if article is None:
                        continue
                    if article.url in seen_urls:
                        continue
                    seen_urls.add(article.url)
                    all_articles.append(article)
            except Exception:
                logger.warning("Failed to fetch feed: %s", feed_url, exc_info=True)

        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=48)
        recent = [
            a for a in all_articles
            if a.published_at.tzinfo is not None and a.published_at >= cutoff
            or a.published_at.tzinfo is None and a.published_at >= cutoff.replace(tzinfo=None)
        ]

        recent.sort(key=lambda a: a.published_at, reverse=True)
        logger.info(
            "%d articles after dedup + 48-hour filter (from %d total)",
            len(recent),
            len(all_articles),
        )
        return recent

    def filter_by_topics(self, articles: List[Article], topics: List[str]) -> List[Article]:
        lowered_topics = [t.lower() for t in topics]
        matched: List[Article] = []
        for article in articles:
            haystack = (article.title + " " + article.raw_content).lower()
            if any(topic in haystack for topic in lowered_topics):
                matched.append(article)
        logger.info(
            "%d / %d articles match topic filters", len(matched), len(articles)
        )
        return matched

    def _parse_feed_entry(self, entry: feedparser.FeedParserDict, source_name: str) -> Optional[Article]:
        try:
            url: str = entry.get("link", "")
            if not url:
                return None

            title: str = entry.get("title", "Untitled")

            # Published date — try multiple fields, fall back to now.
            raw_date: Optional[str] = (
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
            )
            if raw_date:
                try:
                    published_at = dateutil_parser.parse(raw_date)
                except (ValueError, OverflowError):
                    published_at = datetime.now(tz=timezone.utc)
            else:
                published_at = datetime.now(tz=timezone.utc)

            # Raw content — prefer full content, fall back to summary.
            content_html: str = ""
            if entry.get("content"):
                content_html = entry["content"][0].get("value", "")
            if not content_html:
                content_html = entry.get("summary", "")

            raw_content = self._extract_text(content_html)

            # Optional fields.
            author: Optional[str] = entry.get("author") or None
            image_url: Optional[str] = None
            if entry.get("media_thumbnail"):
                image_url = entry["media_thumbnail"][0].get("url")
            elif entry.get("media_content"):
                image_url = entry["media_content"][0].get("url")

            # Estimate read time: ~200 words per minute.
            word_count = len(raw_content.split())
            read_time = max(1, round(word_count / 200))

            return Article(
                id=str(uuid.uuid4()),
                title=title,
                url=url,
                source=source_name,
                published_at=published_at,
                raw_content=raw_content,
                author=author,
                image_url=image_url,
                read_time_minutes=read_time,
            )
        except Exception:
            logger.warning("Failed to parse feed entry", exc_info=True)
            return None

    def _extract_text(self, html_content: str) -> str:
        if not html_content:
            return ""
        try:
            soup = BeautifulSoup(html_content, "lxml")
            return soup.get_text(separator=" ", strip=True)
        except Exception:
            logger.debug("BeautifulSoup parse failed, returning raw content")
            return html_content
