"""Newsletter renderer: produces HTML and plain-text versions of the digest."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_newsletter(articles: list[dict], run_time: datetime, run_id: str | None = None) -> tuple[str, str]:
    """Render the newsletter as (html, plain_text).

    Args:
        articles: List of article dicts sorted by rank (best first).
        run_time: Datetime when the digest run was started.
        run_id: Optional run identifier shown in the edition bar.

    Returns:
        A tuple of (html_string, plain_text_string).
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("newsletter.html.jinja2")

    html = template.render(articles=articles, run_time=run_time, article_count=len(articles), run_id=run_id)

    # Plain-text version
    formatted_date = run_time.strftime("%B %-d, %Y")
    lines: list[str] = [f"THE AGENTIC TIMES\nAI News Digest - {formatted_date}\n{'=' * 50}\n"]
    for rank, article in enumerate(articles, start=1):
        published_at = article.get("published_at")
        if published_at:
            try:
                date_str = published_at.strftime("%b %-d, %Y")
            except AttributeError:
                date_str = str(published_at)
        else:
            date_str = ""

        rank_score = article.get("rank_score", 0)
        author = article.get("author", "")

        meta_parts = [article["publication"]]
        if date_str:
            meta_parts.append(date_str)
        meta_parts.append(f"Score: {rank_score:.1f}/10")

        entry_lines = [
            f"#{rank} {article['title']}",
            " | ".join(meta_parts),
        ]
        if author:
            entry_lines.append(f"By {author}")
        entry_lines.append("")
        entry_lines.append(article["summary"])
        entry_lines.append("")
        entry_lines.append(f"Read more: {article['url']}")

        lines.append("\n".join(entry_lines))
    plain_text = "\n\n".join(lines) + f"\n\n{'=' * 50}\nThe Agentic Times — Automated AI News & Analysis\n"

    return html, plain_text
