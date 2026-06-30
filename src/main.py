"""Pipeline orchestrator for the Agentic AI News Summary Service."""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tqdm import tqdm

from src.config import LLM_PROVIDER, LOOKBACK_HOURS, TOP_N
from src.db import get_session
from src.models import FailedArticle, FilteredArticle, Run, RunArticle, SeenArticle
from src.emailer import send_newsletter
from src.fetcher import fetch_articles
from src.filter import filter_articles
from src.processor import process_articles, summarize_articles
from src.ranker import rank_articles
from src.renderer import render_newsletter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def run_pipeline(dry_run: bool = False, run_id: str | None = None, clean: bool = False) -> str:
    """Execute the full news pipeline.

    Args:
        dry_run: If True, skip email delivery and print HTML to stdout.
        run_id: Optional run ID to use; a UUID is generated if not provided.
        clean: If True, delete seen_articles from the past LOOKBACK_HOURS before fetching.

    Returns:
        The run ID string.

    Raises:
        Exception: Re-raises any exception after persisting the error to the DB.
    """
    if run_id is None:
        run_id = str(uuid.uuid4())

    started_at = datetime.now(timezone.utc)

    with get_session() as session:
        session.add(Run(id=run_id, started_at=started_at.isoformat(), status="running"))

    try:
        if clean:
            # seen_at is an ISO-8601 string; lexicographic comparison is chronological.
            clean_cutoff = (started_at - timedelta(hours=LOOKBACK_HOURS)).isoformat()
            with get_session() as session:
                session.execute(
                    delete(SeenArticle).where(SeenArticle.seen_at >= clean_cutoff)
                )
            tqdm.write(f"[{run_id}] Clean run: cleared seen_articles for past {LOOKBACK_HOURS} hours")

        with tqdm(total=7, desc="Pipeline", leave=True) as bar:
            bar.set_description("Fetching articles")
            articles = fetch_articles()
            tqdm.write(f"[{run_id}] Fetched {len(articles)} articles")
            bar.update(1)

            provider_label = "local llama server" if LLM_PROVIDER == "local" else "Claude"
            bar.set_description(f"Processing with {provider_label}")
            articles = process_articles(articles, run_id=run_id)
            tqdm.write(f"[{run_id}] Processed {len(articles)} articles")
            bar.update(1)

            bar.set_description("Filtering by relevance")
            articles, dropped_articles = filter_articles(articles)
            tqdm.write(f"[{run_id}] Kept {len(articles)} articles, dropped {len(dropped_articles)}")
            bar.update(1)

            if dropped_articles:
                with get_session() as session:
                    session.add_all([
                        FilteredArticle(
                            run_id=run_id,
                            url=a["url"],
                            title=a["title"],
                            publication=a["publication"],
                            relevance_score=a["relevance_score"],
                            relevance_reason=a.get("relevance_reason", ""),
                        )
                        for a in dropped_articles
                    ])

            bar.set_description("Ranking")
            articles = rank_articles(articles)
            tqdm.write(f"[{run_id}] Ranked {len(articles)} articles")
            bar.update(1)

            now_iso = datetime.now(timezone.utc).isoformat()
            seen_rows = [{"url": a["url"], "seen_at": now_iso} for a in articles[:TOP_N]]
            if seen_rows:
                stmt = pg_insert(SeenArticle).values(seen_rows).on_conflict_do_nothing(
                    index_elements=[SeenArticle.url]
                )
                with get_session() as session:
                    session.execute(stmt)

            bar.set_description("Generating summaries")
            articles = summarize_articles(articles, run_id=run_id)
            tqdm.write(f"[{run_id}] Summaries generated for {len(articles)} articles")
            bar.update(1)

            with get_session() as session:
                session.add_all([
                    RunArticle(
                        run_id=run_id,
                        title=a["title"],
                        url=a["url"],
                        publication=a["publication"],
                        published_at=str(a.get("published_at", "")),
                        rank_score=a.get("rank_score"),
                    )
                    for a in articles
                ])

            bar.set_description("Rendering")
            html, plain_text = render_newsletter(articles, started_at)
            tqdm.write(f"[{run_id}] Newsletter rendered")
            bar.update(1)

            if dry_run:
                bar.set_description("Dry-run")
                tqdm.write(f"[{run_id}] Dry-run: printing HTML to stdout")
                print(html)
            else:
                bar.set_description("Sending email")
                send_newsletter(html, plain_text, started_at)
                tqdm.write(f"[{run_id}] Email sent")
            bar.update(1)

        completed_at = datetime.now(timezone.utc)
        with get_session() as session:
            failed_count = session.scalar(
                select(func.count()).select_from(FailedArticle).where(FailedArticle.run_id == run_id)
            ) or 0
            session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status="success",
                    completed_at=completed_at.isoformat(),
                    article_count=len(articles),
                    html=html,
                    dropped_count=len(dropped_articles),
                    failed_count=failed_count,
                )
            )

        tqdm.write(f"[{run_id}] Run complete")
        return run_id

    except Exception as exc:
        log.exception("[%s] Pipeline failed: %s", run_id, exc)
        completed_at = datetime.now(timezone.utc)
        with get_session() as session:
            session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(status="error", completed_at=completed_at.isoformat(), error=str(exc))
            )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic AI News Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip email delivery and print HTML to stdout instead",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete seen_articles from the past 12 hours before fetching",
    )
    args = parser.parse_args()

    try:
        run_pipeline(dry_run=args.dry_run, clean=args.clean)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
