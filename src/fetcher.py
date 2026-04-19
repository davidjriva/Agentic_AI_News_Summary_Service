"""Fetcher module for the Agentic AI news summary service.

Polls RSS feeds and the HN Algolia JSON API, deduplicates against
seen_articles, and returns normalised article dicts.
"""
from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests

from src.config import FEED_URLS, HN_ALGOLIA_URL, LOOKBACK_HOURS, MAX_ARTICLES_PER_SOURCE, SCORE_CACHE_TTL_DAYS
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

    Prunes seen_articles rows older than 7 days and article_scores older than
    SCORE_CACHE_TTL_DAYS. Does NOT write to seen_articles — that is main.py's
    responsibility (top-N only, after ranking).

    Returns a list of dicts with keys:
        title, url, description, author, publication, published_at
    """
    conn = get_connection()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    prune_cutoff = now - timedelta(days=7)

    conn.execute(
        "DELETE FROM seen_articles WHERE seen_at < ?",
        (prune_cutoff.isoformat(),),
    )
    # SCORE_CACHE_TTL_DAYS is a module-level int constant — not user input; f-string is safe
    conn.execute(
        f"DELETE FROM article_scores WHERE cached_at < datetime('now', '-{SCORE_CACHE_TTL_DAYS} days')"
    )
    conn.commit()

    seen_urls: set[str] = {
        row[0]
        for row in conn.execute("SELECT url FROM seen_articles").fetchall()
    }
    conn.close()

    return _fetch_parallel(seen_urls, cutoff, now)


# ---------------------------------------------------------------------------
# Parallel fetch implementation
# ---------------------------------------------------------------------------

def _dispatch_feed(
    feed_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    if feed_url == HN_ALGOLIA_URL:
        return _process_hn(feed_url, now, cutoff, seen_urls)
    return _process_rss(feed_url, cutoff, seen_urls)


def _fetch_parallel(
    seen_urls: set[str],
    cutoff: datetime,
    now: datetime,
) -> list[dict]:
    """Fetch all feeds concurrently using ThreadPoolExecutor.

    All processors receive the same seen_urls snapshot (read-only during
    concurrent execution). Cross-feed URL duplicates are resolved after
    collection via seen_in_run.

    Returns a list of article dicts. Does NOT touch the database.
    """
    articles: list[dict] = []
    seen_in_run: set[str] = set()

    with ThreadPoolExecutor(max_workers=min(10, len(FEED_URLS))) as executor:
        futures = {
            executor.submit(_dispatch_feed, url, now, cutoff, seen_urls): url
            for url in FEED_URLS
        }
        for future in as_completed(futures):
            try:
                feed_articles = future.result()
            except Exception:
                continue
            for article in feed_articles:
                url = article["url"]
                if url not in seen_in_run:
                    seen_in_run.add(url)
                    articles.append(article)

    return articles


# ---------------------------------------------------------------------------
# Per-source processors
# ---------------------------------------------------------------------------

def _process_rss(
    feed_url: str,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    """Parse an RSS/Atom feed and return qualifying articles."""
    articles: list[dict] = []
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return articles

    publication = _domain(feed_url)

    for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
        url = getattr(entry, "link", None)
        if not url:
            continue
        published_at = _parse_struct_time(getattr(entry, "published_parsed", None))
        if not _is_recent(published_at, cutoff):
            continue
        if url in seen_urls:
            continue
        articles.append(_entry_to_dict(entry, url, publication, published_at))

    return articles


def _process_hn(
    hn_url: str,
    now: datetime,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    """Fetch HN Algolia JSON and return qualifying articles."""
    articles: list[dict] = []
    try:
        resp = requests.get(
            hn_url,
            params={"numericFilters": f"created_at_i>{int(cutoff.timestamp())}"},
            timeout=10,
        )
        hits = resp.json().get("hits", [])
    except Exception:
        return articles

    publication = _domain(hn_url)

    for hit in hits[:MAX_ARTICLES_PER_SOURCE]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        if not url:
            continue
        published_at = _parse_iso(hit.get("created_at", ""))
        if not _is_recent(published_at, cutoff):
            continue
        if url in seen_urls:
            continue
        articles.append({
            "title": hit.get("title", ""),
            "url": url,
            "description": hit.get("story_text") or hit.get("comment_text") or "",
            "author": hit.get("author", ""),
            "publication": publication,
            "published_at": published_at,
        })

    return articles


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
