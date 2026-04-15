"""Pipeline orchestrator for the Agentic AI News Summary Service."""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone

from src.config import LLM_PROVIDER
from src.db import get_connection
from src.emailer import send_newsletter
from src.fetcher import fetch_articles
from src.processor import process_articles
from src.ranker import rank_articles
from src.renderer import render_newsletter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def run_pipeline(dry_run: bool = False, run_id: str | None = None) -> str:
    """Execute the full news pipeline.

    Args:
        dry_run: If True, skip email delivery and print HTML to stdout.
        run_id: Optional run ID to use; a UUID is generated if not provided.

    Returns:
        The run ID string.

    Raises:
        Exception: Re-raises any exception after persisting the error to the DB.
    """
    if run_id is None:
        run_id = str(uuid.uuid4())

    started_at = datetime.now(timezone.utc)

    conn = get_connection()
    conn.execute(
        "INSERT INTO runs (id, started_at, status) VALUES (?, ?, ?)",
        (run_id, started_at.isoformat(), "running"),
    )
    conn.commit()
    conn.close()

    try:
        log.info("[%s] Stage 1: Fetching articles…", run_id)
        articles = fetch_articles()
        log.info("[%s] Fetched %d articles", run_id, len(articles))

        conn = get_connection()
        conn.executemany(
            "INSERT INTO run_articles (run_id, title, url, publication, published_at) VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, a["title"], a["url"], a["publication"], str(a.get("published_at", "")))
                for a in articles
            ],
        )
        conn.commit()
        conn.close()

        provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
        log.info("[%s] Stage 2: Processing with %s…", run_id, provider_label)
        articles = process_articles(articles)
        log.info("[%s] Processed %d articles", run_id, len(articles))

        log.info("[%s] Stage 3: Ranking…", run_id)
        articles = rank_articles(articles)
        log.info("[%s] Ranked %d articles", run_id, len(articles))

        log.info("[%s] Stage 4: Rendering newsletter…", run_id)
        html, plain_text = render_newsletter(articles, started_at)
        log.info("[%s] Newsletter rendered", run_id)

        if dry_run:
            log.info("[%s] Dry-run: printing HTML to stdout", run_id)
            print(html)
        else:
            log.info("[%s] Stage 5: Sending email…", run_id)
            send_newsletter(html, plain_text, started_at)
            log.info("[%s] Email sent", run_id)

        completed_at = datetime.now(timezone.utc)
        conn = get_connection()
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, article_count=?, html=? WHERE id=?",
            ("success", completed_at.isoformat(), len(articles), html, run_id),
        )
        conn.commit()
        conn.close()

        log.info("[%s] Run complete", run_id)
        return run_id

    except Exception as exc:
        log.exception("[%s] Pipeline failed: %s", run_id, exc)
        completed_at = datetime.now(timezone.utc)
        conn = get_connection()
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, error=? WHERE id=?",
            ("error", completed_at.isoformat(), str(exc), run_id),
        )
        conn.commit()
        conn.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic AI News Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip email delivery and print HTML to stdout instead",
    )
    args = parser.parse_args()

    try:
        run_pipeline(dry_run=args.dry_run)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
