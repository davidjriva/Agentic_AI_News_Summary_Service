"""Newsletter renderer: produces HTML and plain-text versions of the digest."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_newsletter(
    articles: list[dict],
    run_time: datetime,
    *,
    run_id: str | None = None,
    base_url: str = "http://localhost:8000",
) -> tuple[str, str]:
    """Render the newsletter as (html, plain_text).

    Args:
        articles: List of article dicts sorted by rank (best first).
        run_time: Datetime when the digest run was started.
        run_id: Optional run identifier surfaced in the edition bar.
        base_url: Base URL for the dashboard link in the footer.

    Returns:
        A tuple of (html_string, plain_text_string).
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("newsletter.html.jinja2")

    html = template.render(
        articles=articles,
        run_time=run_time,
        article_count=len(articles),
        run_id=run_id,
        base_url=base_url,
    )

    # Plain-text version
    lines: list[str] = []
    for rank, article in enumerate(articles, start=1):
        lines.append(
            f"#{rank} {article['title']} - {article['publication']}\n"
            f"{article['summary']}\n"
            f"{article['url']}"
        )
    plain_text = "\n\n".join(lines)

    return html, plain_text
