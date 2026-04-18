"""Tests for src/fetcher.py (TDD)."""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Return a fresh sqlite3 connection backed by a temp file."""
    db_path = tmp_path / "test_news.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            url TEXT PRIMARY KEY,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            article_count INTEGER,
            status TEXT,
            html TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _make_entry(url, title="Test Title", author="Test Author",
                description="Test Description",
                published_parsed=None, hours_ago=1):
    """Build a fake feedparser entry as a SimpleNamespace."""
    if published_parsed is None:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        # feedparser returns time.struct_time (9-tuple); we use a tuple here
        published_parsed = dt.timetuple()
    return SimpleNamespace(
        link=url,
        title=title,
        author=author,
        summary=description,
        published_parsed=published_parsed,
    )


def _make_feed(entries):
    """Wrap entries in a fake feedparser feed result."""
    feed_obj = SimpleNamespace(
        feed=SimpleNamespace(title="Test Feed"),
        entries=entries,
    )
    return feed_obj


def _make_hn_hit(url, title="HN Title", author="hn_author",
                 hours_ago=1, object_id="12345"):
    """Build a fake HN Algolia API hit dict."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "objectID": object_id,
        "title": title,
        "url": url,
        "author": author,
        "created_at": dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "story_text": "Some HN story text",
    }


# ---------------------------------------------------------------------------
# (a) Articles older than LOOKBACK_HOURS are filtered out
# ---------------------------------------------------------------------------

def test_old_articles_filtered(tmp_db):
    """Articles published before the lookback window must be excluded."""
    from src.fetcher import fetch_articles

    recent_url = "https://example.com/recent"
    old_url = "https://example.com/old"

    recent_entry = _make_entry(recent_url, hours_ago=1)
    old_entry = _make_entry(old_url, hours_ago=96)

    feed = _make_feed([recent_entry, old_entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        # HN JSON response with no hits
        mock_get.return_value.json.return_value = {"hits": []}

        articles = fetch_articles()

    urls = [a["url"] for a in articles]
    assert recent_url in urls, "Recent article should be included"
    assert old_url not in urls, "Old article should be filtered out"


# ---------------------------------------------------------------------------
# (b) Articles already in seen_articles are skipped
# ---------------------------------------------------------------------------

def test_seen_articles_skipped(tmp_db):
    """URLs already recorded in seen_articles must not appear in results."""
    from src.fetcher import fetch_articles

    seen_url = "https://example.com/already-seen"
    new_url = "https://example.com/brand-new"

    # Pre-populate seen_articles
    tmp_db.execute(
        "INSERT INTO seen_articles (url, seen_at) VALUES (?, datetime('now'))",
        (seen_url,),
    )
    tmp_db.commit()

    entries = [
        _make_entry(seen_url, hours_ago=1),
        _make_entry(new_url, hours_ago=1),
    ]
    feed = _make_feed(entries)

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        articles = fetch_articles()

    urls = [a["url"] for a in articles]
    assert seen_url not in urls, "Already-seen URL must be skipped"
    assert new_url in urls, "New URL should be included"


# ---------------------------------------------------------------------------
# (c) New article URLs are inserted into seen_articles
# ---------------------------------------------------------------------------

def test_new_urls_inserted_into_seen_articles(tmp_db):
    """After fetch_articles(), new URLs must be present in seen_articles."""
    from src.fetcher import fetch_articles

    new_url = "https://example.com/new-article"
    entry = _make_entry(new_url, hours_ago=1)
    feed = _make_feed([entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        fetch_articles()

    row = tmp_db.execute(
        "SELECT url FROM seen_articles WHERE url = ?", (new_url,)
    ).fetchone()
    assert row is not None, "New URL should be inserted into seen_articles"


# ---------------------------------------------------------------------------
# (d) author and publication fields extracted from feedparser entries
# ---------------------------------------------------------------------------

def test_author_and_publication_extracted(tmp_db):
    """author must come from entry; publication must be derived from feed URL domain."""
    from src.fetcher import fetch_articles

    feed_url = "https://techcrunch.com/tag/artificial-intelligence/feed"
    url = "https://techcrunch.com/2024/01/01/some-post"
    entry = _make_entry(url, author="Jane Doe", hours_ago=1)
    feed = _make_feed([entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", [feed_url]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        articles = fetch_articles()

    assert len(articles) == 1
    article = articles[0]
    assert article["author"] == "Jane Doe"
    assert "techcrunch.com" in article["publication"]


def test_missing_author_defaults_to_empty_string(tmp_db):
    """When an entry has no author attribute, author must be empty string."""
    from src.fetcher import fetch_articles

    url = "https://example.com/no-author"
    entry = _make_entry(url, hours_ago=1)
    # Remove the author attribute to simulate a missing author
    del entry.author
    feed = _make_feed([entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        articles = fetch_articles()

    assert len(articles) == 1
    assert articles[0]["author"] == ""


# ---------------------------------------------------------------------------
# (e) HN Algolia JSON hits parsed into standard article dict shape
# ---------------------------------------------------------------------------

def test_hn_algolia_parsed_correctly(tmp_db):
    """HN hits must be parsed into the same shape as RSS articles."""
    from src.fetcher import fetch_articles

    hn_url = "https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI"
    hit = _make_hn_hit(
        url="https://news.ycombinator.com/item?id=12345",
        title="Agentic AI breakthrough",
        author="hn_user",
        hours_ago=2,
        object_id="12345",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"hits": [hit]}

    with patch("src.fetcher.feedparser.parse", return_value=_make_feed([])), \
         patch("src.fetcher.requests.get", return_value=mock_response), \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", [hn_url]), \
         patch("src.fetcher.HN_ALGOLIA_URL", hn_url):

        articles = fetch_articles()

    assert len(articles) == 1
    article = articles[0]

    # Verify all required keys and types
    assert isinstance(article["title"], str) and article["title"] == "Agentic AI breakthrough"
    assert isinstance(article["url"], str)
    assert isinstance(article["description"], str)
    assert isinstance(article["author"], str) and article["author"] == "hn_user"
    assert isinstance(article["publication"], str)
    assert isinstance(article["published_at"], datetime)


def test_hn_algolia_old_hits_filtered(tmp_db):
    """HN hits older than LOOKBACK_HOURS must be filtered out."""
    from src.fetcher import fetch_articles

    hn_url = "https://hn.algolia.com/api/v1/search?tags=story&query=agentic+AI"
    recent_hit = _make_hn_hit(
        url="https://news.ycombinator.com/item?id=111",
        hours_ago=1,
        object_id="111",
    )
    old_hit = _make_hn_hit(
        url="https://news.ycombinator.com/item?id=222",
        hours_ago=96,
        object_id="222",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"hits": [recent_hit, old_hit]}

    with patch("src.fetcher.feedparser.parse", return_value=_make_feed([])), \
         patch("src.fetcher.requests.get", return_value=mock_response), \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", [hn_url]), \
         patch("src.fetcher.HN_ALGOLIA_URL", hn_url):

        articles = fetch_articles()

    urls = [a["url"] for a in articles]
    assert "https://news.ycombinator.com/item?id=111" in urls
    assert "https://news.ycombinator.com/item?id=222" not in urls


# ---------------------------------------------------------------------------
# Article dict shape completeness
# ---------------------------------------------------------------------------

def test_article_dict_has_all_required_keys(tmp_db):
    """Every returned article dict must have the 6 required keys."""
    from src.fetcher import fetch_articles

    url = "https://example.com/article"
    entry = _make_entry(url, hours_ago=1)
    feed = _make_feed([entry])

    with patch("src.fetcher.feedparser.parse", return_value=feed), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        articles = fetch_articles()

    assert len(articles) == 1
    required_keys = {"title", "url", "description", "author", "publication", "published_at"}
    assert required_keys.issubset(articles[0].keys())
