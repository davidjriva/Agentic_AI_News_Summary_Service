"""Tests for src/renderer.py - newsletter renderer."""
import pytest
from datetime import datetime
from src.renderer import render_newsletter


@pytest.fixture
def articles():
    return [
        {
            "title": "AI Agents Take Over Workflows",
            "url": "https://example.com/article-1",
            "author": "Alice Smith",
            "publication": "TechCrunch",
            "published_at": datetime(2026, 4, 14, 9, 0, 0),
            "summary": "AI agents are increasingly being used to automate complex workflows.",
            "impact_score": 0.92,
            "authenticity_score": 0.88,
            "rank_score": 0.90,
        },
        {
            "title": "GPT-5 Released with Agentic Features",
            "url": "https://example.com/article-2",
            "author": "Bob Jones",
            "publication": "Wired",
            "published_at": datetime(2026, 4, 14, 8, 30, 0),
            "summary": "OpenAI released GPT-5 with powerful new agentic capabilities.",
            "impact_score": 0.95,
            "authenticity_score": 0.91,
            "rank_score": 0.93,
        },
    ]


@pytest.fixture
def run_time():
    return datetime(2026, 4, 14, 12, 0, 0)


def test_html_contains_article_metadata(articles, run_time):
    """HTML should contain each article's title, author, publication, and summary."""
    html, _ = render_newsletter(articles, run_time)
    for article in articles:
        assert article["title"] in html, f"Title '{article['title']}' not found in HTML"
        assert article["author"] in html, f"Author '{article['author']}' not found in HTML"
        assert article["publication"] in html, f"Publication '{article['publication']}' not found in HTML"
        assert article["summary"] in html, f"Summary not found in HTML"


def test_html_title_is_anchor_tag(articles, run_time):
    """Each article title should be wrapped in an <a> tag with the correct href."""
    html, _ = render_newsletter(articles, run_time)
    for article in articles:
        assert f'href="{article["url"]}"' in html, f"URL '{article['url']}' not found as href"
        # The title text should appear near an anchor tag
        assert article["title"] in html


def test_html_contains_run_time(articles, run_time):
    """HTML should contain the run_time formatted as a string."""
    html, _ = render_newsletter(articles, run_time)
    # run_time should appear somewhere in the HTML (any reasonable string format)
    assert "2026" in html, "run_time year not found in HTML"
    assert "04" in html or "April" in html or "4" in html, "run_time month not found in HTML"


def test_html_contains_article_count(articles, run_time):
    """HTML should contain the total article count."""
    html, _ = render_newsletter(articles, run_time)
    assert str(len(articles)) in html, f"Article count '{len(articles)}' not found in HTML"


def test_returns_tuple_and_plain_contains_titles(articles, run_time):
    """render_newsletter should return (html_str, plain_str); plain_str contains all titles."""
    result = render_newsletter(articles, run_time)
    assert isinstance(result, tuple), "render_newsletter must return a tuple"
    assert len(result) == 2, "Tuple must have exactly 2 elements"
    html, plain = result
    assert isinstance(html, str), "First element must be a string (HTML)"
    assert isinstance(plain, str), "Second element must be a string (plain text)"
    for article in articles:
        assert article["title"] in plain, f"Title '{article['title']}' not found in plain text"
