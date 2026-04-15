"""Fetcher module for the Agentic AI news summary service.

Polls RSS feeds and the HN Algolia JSON API, deduplicates against
seen_articles, and returns normalised article dicts.
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests

from src.config import FEED_URLS, HN_ALGOLIA_URL, LOOKBACK_HOURS, MAX_ARTICLES_PER_SOURCE
from src.db import get_connection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    """Return the netloc (domain) portion of *url*, e.g. 'techcrunch.com'."""
    return urlparse(url).netloc


def _parse_struct_time(st) -> datetime | None:
    """Convert a ``time.struct_time`` (or 9-tuple) to an aware UTC datetime."""
    if st is None:
        return None
    try:
        ts = calendar.timegm(st)  # interprets struct_time as UTC
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def _parse_iso(iso_str: str) -> datetime | None:
    """Parse an ISO-8601 string like '2024-01-01T12:00:00.000Z' to UTC datetime."""
    if not iso_str:
        return None
    try:
        # Replace trailing Z with +00:00 for fromisoformat compatibility
        normalized = iso_str.replace("Z", "+00:00")
        # Remove sub-second precision if present for Python < 3.11
        if "." in normalized:
            base, rest = normalized.split(".", 1)
            # rest may look like "000+00:00"; strip digits, keep tz
            tz_part = rest.lstrip("0123456789")
            normalized = base + tz_part
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def _is_recent(published_at: datetime | None, cutoff: datetime) -> bool:
    """Return True when *published_at* is at or after *cutoff*."""
    if published_at is None:
        return False
    # Ensure both are offset-aware
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at >= cutoff


# ---------------------------------------------------------------------------
# Core fetch function
# ---------------------------------------------------------------------------

def fetch_articles() -> list[dict]:
    """Fetch and return new articles across all configured sources.

    For each source:
    * Parse published_at to a UTC-aware datetime.
    * Discard articles older than LOOKBACK_HOURS.
    * Skip URLs already present in seen_articles.
    * Insert new URLs into seen_articles.

    Also prunes seen_articles rows older than 7 days.

    Returns a list of dicts with keys:
        title, url, description, author, publication, published_at
    """
    conn = get_connection()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    prune_cutoff = now - timedelta(days=7)

    # Prune stale dedup records
    conn.execute(
        "DELETE FROM seen_articles WHERE seen_at < ?",
        (prune_cutoff.isoformat(),),
    )
    conn.commit()

    # Load the current set of seen URLs for O(1) lookup
    seen_urls: set[str] = {
        row[0]
        for row in conn.execute("SELECT url FROM seen_articles").fetchall()
    }

    articles: list[dict] = []
    new_urls: list[tuple[str, str]] = []  # (url, seen_at ISO)

    for feed_url in FEED_URLS:
        if feed_url == HN_ALGOLIA_URL:
            _process_hn(
                feed_url, now, cutoff, seen_urls, articles, new_urls
            )
        else:
            _process_rss(
                feed_url, cutoff, seen_urls, articles, new_urls
            )

    # Persist all newly-seen URLs in one batch
    if new_urls:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_articles (url, seen_at) VALUES (?, ?)",
            new_urls,
        )
        conn.commit()

    return articles


# ---------------------------------------------------------------------------
# Per-source processors
# ---------------------------------------------------------------------------

def _process_rss(
    feed_url: str,
    cutoff: datetime,
    seen_urls: set[str],
    articles: list[dict],
    new_urls: list[tuple[str, str]],
) -> None:
    """Parse an RSS/Atom feed and append qualifying articles."""
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return

    publication = _domain(feed_url)
    now_iso = datetime.now(timezone.utc).isoformat()

    for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
        url = getattr(entry, "link", None)
        if not url:
            continue

        published_at = _parse_struct_time(getattr(entry, "published_parsed", None))
        if not _is_recent(published_at, cutoff):
            continue

        if url in seen_urls:
            continue

        article = _entry_to_dict(entry, url, publication, published_at)
        articles.append(article)
        seen_urls.add(url)
        new_urls.append((url, now_iso))


def _process_hn(
    hn_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
    articles: list[dict],
    new_urls: list[tuple[str, str]],
) -> None:
    """Fetch HN Algolia JSON and append qualifying articles."""
    try:
        resp = requests.get(hn_url)
        hits = resp.json().get("hits", [])
    except Exception:
        return

    publication = _domain(hn_url)
    now_iso = now.isoformat()

    for hit in hits[:MAX_ARTICLES_PER_SOURCE]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        if not url:
            continue

        published_at = _parse_iso(hit.get("created_at", ""))
        if not _is_recent(published_at, cutoff):
            continue

        if url in seen_urls:
            continue

        article = {
            "title": hit.get("title", ""),
            "url": url,
            "description": hit.get("story_text") or hit.get("comment_text") or "",
            "author": hit.get("author", ""),
            "publication": publication,
            "published_at": published_at,
        }
        articles.append(article)
        seen_urls.add(url)
        new_urls.append((url, now_iso))


# ---------------------------------------------------------------------------
# Entry normaliser
# ---------------------------------------------------------------------------

def _entry_to_dict(
    entry,
    url: str,
    publication: str,
    published_at: datetime,
) -> dict:
    """Convert a feedparser entry to a normalised article dict."""
    return {
        "title": getattr(entry, "title", ""),
        "url": url,
        "description": getattr(entry, "summary", "") or getattr(entry, "description", "") or "",
        "author": getattr(entry, "author", ""),
        "publication": publication,
        "published_at": published_at,
    }
