"""Relevance filter: splits processed articles into kept and dropped."""
from src import config as _cfg


def filter_articles(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split articles by relevance_score threshold.

    Returns:
        (kept, dropped) — kept articles score >= RELEVANCE_THRESHOLD; dropped score below.
    """
    kept, dropped = [], []
    for article in articles:
        if article.get("relevance_score", 0) >= _cfg.RELEVANCE_THRESHOLD:
            kept.append(article)
        else:
            dropped.append(article)
    return kept, dropped
