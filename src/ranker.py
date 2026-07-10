from src.config import TOP_N, MAX_PER_NEWSLETTER_SOURCE


def rank_articles(articles: list[dict]) -> list[dict]:
    """Rank articles by weighted score and return top TOP_N with source diversity.

    rank_score = (impact_score * 0.5) + (relevance_score * 0.3) + (authenticity_score * 0.2)

    Articles are sorted descending by rank_score, ties broken by url ascending.
    At most MAX_PER_NEWSLETTER_SOURCE articles from any single publication are
    included for source diversity. Only the top TOP_N articles are returned.
    """
    scored = []
    for article in articles:
        rank_score = (
            article["impact_score"] * 0.5
            + article.get("relevance_score", 0) * 0.3
            + article["authenticity_score"] * 0.2
        )
        scored.append({**article, "rank_score": rank_score})

    scored.sort(key=lambda a: (-a["rank_score"], a["url"]))

    results: list[dict] = []
    source_counts: dict[str, int] = {}

    for article in scored:
        if len(results) >= TOP_N:
            break
        pub = article.get("publication", "")
        if source_counts.get(pub, 0) >= MAX_PER_NEWSLETTER_SOURCE:
            continue
        results.append(article)
        source_counts[pub] = source_counts.get(pub, 0) + 1

    return results
