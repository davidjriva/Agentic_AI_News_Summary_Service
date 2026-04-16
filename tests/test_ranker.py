import pytest
from src.ranker import rank_articles
from src.config import TOP_N, MAX_PER_NEWSLETTER_SOURCE


def make_article(url: str, impact: float, authenticity: float, publication: str = "example.com") -> dict:
    return {"url": url, "impact_score": impact, "authenticity_score": authenticity, "publication": publication}


def expected_rank(impact: float, authenticity: float) -> float:
    return impact * 0.6 + authenticity * 0.4


class TestRankScoreComputation:
    def test_rank_score_added_to_article(self):
        articles = [make_article("http://a.com", 8.0, 6.0)]
        result = rank_articles(articles)
        assert "rank_score" in result[0]

    def test_rank_score_formula(self):
        articles = [make_article("http://a.com", 8.0, 6.0)]
        result = rank_articles(articles)
        assert result[0]["rank_score"] == pytest.approx(expected_rank(8.0, 6.0))

    def test_rank_score_formula_various(self):
        articles = [
            make_article("http://a.com", 10.0, 10.0),
            make_article("http://b.com", 1.0, 1.0),
            make_article("http://c.com", 5.0, 7.5),
        ]
        result = rank_articles(articles)
        scores = {a["url"]: a["rank_score"] for a in result}
        assert scores["http://a.com"] == pytest.approx(expected_rank(10.0, 10.0))
        assert scores["http://b.com"] == pytest.approx(expected_rank(1.0, 1.0))
        assert scores["http://c.com"] == pytest.approx(expected_rank(5.0, 7.5))


class TestSortOrder:
    def test_sorted_descending_by_rank_score(self):
        articles = [
            make_article("http://low.com", 2.0, 2.0),
            make_article("http://high.com", 9.0, 9.0),
            make_article("http://mid.com", 5.0, 5.0),
        ]
        result = rank_articles(articles)
        scores = [a["rank_score"] for a in result]
        assert scores == sorted(scores, reverse=True)

    def test_first_article_has_highest_rank(self):
        articles = [
            make_article("http://low.com", 1.0, 1.0),
            make_article("http://high.com", 10.0, 10.0),
        ]
        result = rank_articles(articles)
        assert result[0]["url"] == "http://high.com"


class TestTopNLimit:
    def test_returns_at_most_top_n(self):
        # Each article has a unique publication so the per-source cap never fires;
        # the TOP_N limit is the only constraint under test here.
        articles = [
            make_article(f"http://article{i}.com", float(i % 10 + 1), float(i % 10 + 1),
                         publication=f"source{i}.com")
            for i in range(TOP_N + 5)
        ]
        result = rank_articles(articles)
        assert len(result) <= TOP_N

    def test_returns_top_n_exactly_when_input_exceeds_top_n(self):
        articles = [
            make_article(f"http://article{i}.com", float(i % 10 + 1), float(i % 10 + 1),
                         publication=f"source{i}.com")
            for i in range(TOP_N + 10)
        ]
        result = rank_articles(articles)
        assert len(result) == TOP_N

    def test_returns_all_when_input_less_than_top_n(self):
        n = TOP_N - 3
        articles = [
            make_article(f"http://article{i}.com", 5.0, 5.0,
                         publication=f"source{i}.com")
            for i in range(n)
        ]
        result = rank_articles(articles)
        assert len(result) == n


class TestTieBreaking:
    def test_equal_rank_scores_ordered_by_url_ascending(self):
        articles = [
            make_article("http://z-site.com", 5.0, 5.0),
            make_article("http://a-site.com", 5.0, 5.0),
            make_article("http://m-site.com", 5.0, 5.0),
        ]
        result = rank_articles(articles)
        urls = [a["url"] for a in result]
        assert urls == sorted(urls)

    def test_tie_breaking_is_stable_across_calls(self):
        articles = [
            make_article("http://b.com", 7.0, 7.0),
            make_article("http://a.com", 7.0, 7.0),
        ]
        result1 = rank_articles(articles)
        result2 = rank_articles(list(reversed(articles)))
        assert [a["url"] for a in result1] == [a["url"] for a in result2]

    def test_tie_breaking_only_applies_when_scores_equal(self):
        articles = [
            make_article("http://z.com", 9.0, 9.0),
            make_article("http://a.com", 1.0, 1.0),
        ]
        result = rank_articles(articles)
        assert result[0]["url"] == "http://z.com"


class TestSourceDiversity:
    """rank_articles must not allow a single publication to dominate the output."""

    def test_single_source_capped_at_max_per_newsletter_source(self):
        """If all articles come from one publication, result is capped at MAX_PER_NEWSLETTER_SOURCE."""
        articles = [
            make_article(f"http://arxiv.org/abs/{i}", 9.0, 9.0, publication="arxiv.org")
            for i in range(MAX_PER_NEWSLETTER_SOURCE + 5)
        ]
        result = rank_articles(articles)
        arxiv_count = sum(1 for a in result if a["publication"] == "arxiv.org")
        assert arxiv_count <= MAX_PER_NEWSLETTER_SOURCE

    def test_lower_ranked_article_included_for_diversity(self):
        """A lower-ranked article from a second source is preferred over an extra article
        from a source that has already hit its per-source cap."""
        # Fill more than the cap from arxiv
        arxiv_articles = [
            make_article(f"http://arxiv.org/abs/{i}", 9.0, 9.0, publication="arxiv.org")
            for i in range(MAX_PER_NEWSLETTER_SOURCE + 2)
        ]
        # One lower-scored article from a different source
        other_article = make_article("http://techcrunch.com/story", 3.0, 3.0, publication="techcrunch.com")
        result = rank_articles(arxiv_articles + [other_article])

        result_urls = [a["url"] for a in result]
        assert "http://techcrunch.com/story" in result_urls, (
            "Lower-ranked article from a different source must appear once arxiv hits its cap"
        )

    def test_diverse_sources_all_represented(self):
        """When each source has equally-scored articles, multiple sources appear in results."""
        sources = ["arxiv.org", "techcrunch.com", "venturebeat.com", "wired.com", "theverge.com"]
        articles = []
        for i, pub in enumerate(sources):
            for j in range(MAX_PER_NEWSLETTER_SOURCE + 2):
                articles.append(
                    make_article(f"http://{pub}/article/{j}", 8.0, 8.0, publication=pub)
                )
        result = rank_articles(articles)
        pubs_in_result = {a["publication"] for a in result}
        assert len(pubs_in_result) > 1, "Multiple sources must appear in the ranked output"

    def test_per_source_cap_does_not_reduce_total_below_available_diversity(self):
        """When there is enough diversity, the total result count still reaches TOP_N."""
        # 10 sources × (MAX_PER_NEWSLETTER_SOURCE + 2) articles each → plenty to fill TOP_N
        sources = [f"source{i}.com" for i in range(10)]
        articles = [
            make_article(f"http://{pub}/article/{j}", 8.0, 8.0, publication=pub)
            for pub in sources
            for j in range(MAX_PER_NEWSLETTER_SOURCE + 2)
        ]
        result = rank_articles(articles)
        assert len(result) == TOP_N
