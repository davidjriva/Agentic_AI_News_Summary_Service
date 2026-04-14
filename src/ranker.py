from src.config import TOP_N


def rank_articles(articles: list[dict]) -> list[dict]:
    """Rank articles by weighted score and return top TOP_N.

    rank_score = (impact_score * 0.6) + (authenticity_score * 0.4)

    Articles are sorted descending by rank_score, with ties broken by url ascending.
    Only the top TOP_N articles are returned.
    """
    scored = []
    for article in articles:
        rank_score = article["impact_score"] * 0.6 + article["authenticity_score"] * 0.4
        scored.append({**article, "rank_score": rank_score})

    scored.sort(key=lambda a: (-a["rank_score"], a["url"]))

    return scored[:TOP_N]
