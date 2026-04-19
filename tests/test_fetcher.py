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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS article_scores (
            url                 TEXT PRIMARY KEY,
            impact_score        INTEGER,
            authenticity_score  INTEGER,
            relevance_score     INTEGER,
            impact_reason       TEXT,
            authenticity_reason TEXT,
            relevance_reason    TEXT,
            cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
# (c) fetch_articles does NOT insert into seen_articles; prunes article_scores
# ---------------------------------------------------------------------------

def test_fetcher_does_not_insert_seen_articles(tmp_db, tmp_path):
    """fetch_articles() must not write to seen_articles — that is main.py's responsibility."""
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

    # Re-open fresh connection since fetch_articles() closes the patched connection
    conn2 = sqlite3.connect(str(tmp_path / "test_news.db"))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT url FROM seen_articles WHERE url = ?", (new_url,)
    ).fetchone()
    conn2.close()
    assert row is None, "fetch_articles() must not insert into seen_articles"


def test_fetcher_prunes_article_scores(tmp_db, tmp_path):
    """fetch_articles() must delete article_scores rows older than 3 days."""
    from src.fetcher import fetch_articles

    stale_url = "https://example.com/stale"
    fresh_url = "https://example.com/fresh"

    tmp_db.execute(
        "INSERT INTO article_scores (url, impact_score, authenticity_score, relevance_score, "
        "impact_reason, authenticity_reason, relevance_reason, cached_at) "
        "VALUES (?, 5, 5, 5, 'r', 'r', 'r', datetime('now', '-4 days'))",
        (stale_url,),
    )
    tmp_db.execute(
        "INSERT INTO article_scores (url, impact_score, authenticity_score, relevance_score, "
        "impact_reason, authenticity_reason, relevance_reason, cached_at) "
        "VALUES (?, 8, 7, 9, 'r', 'r', 'r', datetime('now'))",
        (fresh_url,),
    )
    tmp_db.commit()

    with patch("src.fetcher.feedparser.parse", return_value=_make_feed([])), \
         patch("src.fetcher.requests.get") as mock_get, \
         patch("src.fetcher.get_connection", return_value=tmp_db), \
         patch("src.fetcher.FEED_URLS", ["https://example.com/feed"]), \
         patch("src.fetcher.HN_ALGOLIA_URL", "https://hn.algolia.com/not-used"):

        mock_get.return_value.json.return_value = {"hits": []}
        fetch_articles()

    # Re-open fresh connection since fetch_articles() closes the patched connection
    conn2 = sqlite3.connect(str(tmp_path / "test_news.db"))
    conn2.row_factory = sqlite3.Row
    stale_row = conn2.execute(
        "SELECT url FROM article_scores WHERE url = ?", (stale_url,)
    ).fetchone()
    fresh_row = conn2.execute(
        "SELECT url FROM article_scores WHERE url = ?", (fresh_url,)
    ).fetchone()
    conn2.close()
    assert stale_row is None, "Stale cache entry (>3 days) must be pruned"
    assert fresh_row is not None, "Fresh cache entry must be kept"


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


# ---------------------------------------------------------------------------
# _process_langchain — crash safety: h2 with no /blog/ ancestor link
# ---------------------------------------------------------------------------

def test_langchain_no_blog_link_returns_empty():
    """_process_langchain must return [] when an h2 has no /blog/ ancestor link."""
    from src.fetcher import _process_langchain
    from datetime import datetime, timezone
    from unittest.mock import MagicMock, patch

    # Shallow HTML: h2 is a direct child of body — only 2 parent levels before document root
    html = """
    <html><body>
      <h2 class="t-heading-6-rg">Orphan Title</h2>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime.now(timezone.utc)
    cutoff = now  # nothing passes recency anyway

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []


def test_langchain_happy_path_returns_article():
    """_process_langchain returns a correctly shaped article for a recent post."""
    from src.fetcher import _process_langchain

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Agent Engineering Deep Dive</h2>
        <div class="date-color">April 17, 2026</div>
        <div class="text-c-blue-light-500">Jane Smith</div>
        <a href="/blog/agent-engineering-deep-dive" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert len(result) == 1
    article = result[0]
    assert article["title"] == "Agent Engineering Deep Dive"
    assert article["url"] == "https://www.langchain.com/blog/agent-engineering-deep-dive"
    assert article["author"] == "Jane Smith"
    assert article["publication"] == "www.langchain.com"
    assert article["published_at"] == datetime(2026, 4, 17, tzinfo=timezone.utc)
    assert article["description"] == ""


def test_langchain_old_article_filtered():
    """_process_langchain must exclude articles published before the cutoff."""
    from src.fetcher import _process_langchain

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Stale Post</h2>
        <div class="date-color">January 1, 2025</div>
        <div class="text-c-blue-light-500">Old Author</div>
        <a href="/blog/stale-post" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []


def test_langchain_seen_url_skipped():
    """_process_langchain must skip URLs already present in seen_urls."""
    from src.fetcher import _process_langchain

    url = "https://www.langchain.com/blog/already-seen"

    html = """
    <html><body>
      <div class="blog-card">
        <h2 class="t-heading-6-rg">Already Seen</h2>
        <div class="date-color">April 17, 2026</div>
        <div class="text-c-blue-light-500">Author</div>
        <a href="/blog/already-seen" class="w-inline-block"></a>
      </div>
    </body></html>
    """

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain(
            "https://www.langchain.com/blog", now, cutoff, {url}
        )

    assert result == []


def test_langchain_http_error_returns_empty():
    """_process_langchain must return [] when the HTTP request fails."""
    from src.fetcher import _process_langchain
    import requests as req_lib

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", side_effect=req_lib.RequestException("timeout")):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []


def test_langchain_missing_html_structure_returns_empty():
    """_process_langchain must return [] when the page has no expected h2 elements."""
    from src.fetcher import _process_langchain

    html = "<html><body><p>Nothing here.</p></body></html>"

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status.return_value = None

    now = datetime(2026, 4, 18, tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=72)

    with patch("src.fetcher.requests.get", return_value=mock_resp):
        result = _process_langchain("https://www.langchain.com/blog", now, cutoff, set())

    assert result == []
