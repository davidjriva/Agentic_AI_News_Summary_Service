from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import schedule
import time

from config import Config
from src.email_sender import EmailSender
from src.news_fetcher import NewsFetcher
from src.newsletter_generator import NewsletterGenerator
from src.summarizer import NewsletterSummarizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).parent / "output"


def run_newsletter_pipeline() -> None:
    logger.info("═" * 60)
    logger.info("Starting newsletter pipeline")

    # 1. Load and validate config
    config = Config()
    try:
        config.validate()
    except ValueError as exc:
        logger.error("Configuration invalid:\n%s", exc)
        sys.exit(1)

    # 2. Fetch articles
    logger.info("Fetching articles from %d feed(s)…", len(config.news_sources))
    fetcher = NewsFetcher(config)
    articles = fetcher.fetch_all_feeds()
    logger.info("Fetched %d articles after filtering", len(articles))

    if not articles:
        logger.warning("No articles fetched; aborting pipeline")
        return

    # 3. Filter by topics
    logger.info("Filtering by %d topic(s)…", len(config.TOPICS))
    articles = fetcher.filter_by_topics(articles, config.TOPICS)

    if not articles:
        logger.warning("No articles match the topic filters; aborting pipeline")
        return

    # 4. Take top 15
    articles = articles[:15]
    logger.info("Processing top %d articles", len(articles))

    # 5. Summarize
    logger.info("Summarizing articles with Claude…")
    summarizer = NewsletterSummarizer(config)
    articles = summarizer.summarize_articles(articles)

    # 6. Build newsletter + render
    logger.info("Building newsletter…")
    generator = NewsletterGenerator(config)
    newsletter = generator.build_newsletter(articles)

    intro = summarizer.generate_newsletter_intro(
        newsletter.title,
        [a.title for a in articles],
    )
    logger.info("Newsletter intro: %s", intro)

    html_content = generator.render_html(newsletter)
    plaintext_content = generator.render_plaintext(newsletter)
    logger.info(
        "Newsletter built: issue #%d, %d articles across %d section(s)",
        newsletter.issue_number,
        newsletter.article_count,
        len(newsletter.sections),
    )

    # 7. Save HTML to output/
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"newsletter_{datetime.now().strftime('%Y-%m-%d')}.html"
    output_path = _OUTPUT_DIR / output_filename
    output_path.write_text(html_content, encoding="utf-8")
    logger.info("HTML saved to %s", output_path)

    # 8. Send email if SMTP is configured
    if config.smtp_user and config.smtp_password and config.recipient_emails:
        logger.info(
            "Sending newsletter to %d recipient(s)…", len(config.recipient_emails)
        )
        sender = EmailSender(config)
        subject = (
            f"{config.newsletter_title} — {newsletter.date_display} "
            f"(Issue #{newsletter.issue_number})"
        )
        success = sender.send_newsletter(html_content, plaintext_content, subject)
        if success:
            logger.info("Newsletter sent successfully")
        else:
            logger.error("Failed to send newsletter via email")
    else:
        logger.info(
            "SMTP not fully configured or no recipients — skipping email delivery"
        )

    logger.info("Pipeline complete")
    logger.info("═" * 60)


def schedule_daily_run() -> None:
    config = Config()
    send_time = config.send_time
    logger.info("Scheduling daily newsletter run at %s", send_time)
    schedule.every().day.at(send_time).do(run_newsletter_pipeline)

    logger.info("Scheduler running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_newsletter_pipeline()
