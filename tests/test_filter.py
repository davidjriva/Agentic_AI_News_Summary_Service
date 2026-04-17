import pytest
from unittest.mock import patch
import src.config as _cfg
from src.filter import filter_articles


def make_article(relevance_score: int, url: str = "http://example.com") -> dict:
    return {
        "url": url,
        "title": "Test Article",
        "publication": "example.com",
        "relevance_score": relevance_score,
        "relevance_reason": "test reason",
        "impact_score": 7,
        "authenticity_score": 7,
        "summary": "summary",
        "impact_reason": "",
        "authenticity_reason": "",
    }


class TestFilterArticles:
    def test_returns_tuple_of_kept_and_dropped(self):
        articles = [make_article(8), make_article(3)]
        kept, dropped = filter_articles(articles)
        assert isinstance(kept, list)
        assert isinstance(dropped, list)

    def test_article_at_threshold_is_kept(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(6)])
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_article_below_threshold_is_dropped(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(5)])
        assert len(kept) == 0
        assert len(dropped) == 1

    def test_article_above_threshold_is_kept(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            kept, dropped = filter_articles([make_article(9)])
        assert len(kept) == 1
        assert len(dropped) == 0

    def test_mixed_articles_split_correctly(self):
        with patch.object(_cfg, "RELEVANCE_THRESHOLD", 6):
            articles = [
                make_article(9, "http://a.com"),
                make_article(4, "http://b.com"),
                make_article(6, "http://c.com"),
                make_article(2, "http://d.com"),
            ]
            kept, dropped = filter_articles(articles)
        assert {a["url"] for a in kept} == {"http://a.com", "http://c.com"}
        assert {a["url"] for a in dropped} == {"http://b.com", "http://d.com"}

    def test_empty_input_returns_empty_tuples(self):
        kept, dropped = filter_articles([])
        assert kept == []
        assert dropped == []
