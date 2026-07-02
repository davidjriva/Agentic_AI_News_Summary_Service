"""Email delivery module for the Agentic AI News Summary Service.

The newsletter is sent to every ``confirmed`` subscriber individually (one
message each, single ``To:`` address) so that each email carries that
subscriber's own unsubscribe link and recipients are never exposed to one
another.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src import config as _cfg
from src.db import get_session
from src.models import NewsletterDelivery, Subscriber

log = logging.getLogger(__name__)

# Literal placeholder the renderer puts in the footer; substituted per recipient.
UNSUBSCRIBE_PLACEHOLDER = "%%UNSUBSCRIBE_URL%%"


def _confirmed_subscribers() -> list[tuple[str, str]]:
    """Return (email, unsubscribe_token) for every confirmed subscriber."""
    with get_session() as session:
        rows = session.execute(
            select(Subscriber.email, Subscriber.unsubscribe_token).where(
                Subscriber.status == "confirmed"
            )
        ).all()
    return [(row.email, row.unsubscribe_token) for row in rows]


def _record_deliveries(run_id: str, results: list[tuple[str, str, str | None]]) -> None:
    """Upsert per-subscriber send results into newsletter_deliveries (idempotent per run)."""
    if not results:
        return
    rows = [{"run_id": run_id, "email": e, "status": s, "error": err} for e, s, err in results]
    stmt = pg_insert(NewsletterDelivery).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_delivery_run_email",
        set_={"status": stmt.excluded.status, "error": stmt.excluded.error, "sent_at": func.now()},
    )
    with get_session() as session:
        session.execute(stmt)


def send_newsletter(
    html: str, plain_text: str, run_time: datetime, run_id: str | None = None
) -> None:
    """Send the newsletter to all confirmed subscribers via Gmail SMTP.

    One message per subscriber, each with a personalized unsubscribe link and
    ``List-Unsubscribe`` headers. A failure sending to one recipient is logged
    and skipped — it does not abort the rest of the batch. When ``run_id`` is
    given, each send is recorded in ``newsletter_deliveries`` (sent/failed).

    Raises:
        RuntimeError: If required credentials / PORTFOLIO_BASE_URL are missing.
    """
    subscribers = _confirmed_subscribers()
    if not subscribers:
        log.info("No confirmed subscribers; skipping newsletter send.")
        return

    sender = os.getenv("GMAIL_SENDER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        raise RuntimeError("GMAIL_SENDER / GMAIL_APP_PASSWORD must be set to send email")
    if not _cfg.PORTFOLIO_BASE_URL:
        raise RuntimeError("PORTFOLIO_BASE_URL must be set — unsubscribe links require it")

    am_pm = "AM" if run_time.hour < 12 else "PM"
    subject = f"Agentic AI Digest - {run_time.strftime('%B %d, %Y')} {am_pm}"

    results: list[tuple[str, str, str | None]] = []  # (email, status, error)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(sender, password)

        for email_addr, token in subscribers:
            unsub_url = f"{_cfg.PORTFOLIO_BASE_URL}/newsletter/unsubscribe?token={token}"
            per_html = html.replace(UNSUBSCRIBE_PLACEHOLDER, unsub_url)
            per_plain = plain_text.replace(UNSUBSCRIBE_PLACEHOLDER, unsub_url)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = email_addr
            # RFC 8058 one-click unsubscribe (mail clients POST to the link).
            msg["List-Unsubscribe"] = f"<{unsub_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            msg.attach(MIMEText(per_plain, "plain"))
            msg.attach(MIMEText(per_html, "html"))

            try:
                smtp.sendmail(sender, [email_addr], msg.as_string())
                results.append((email_addr, "sent", None))
            except smtplib.SMTPException as exc:
                log.warning("Failed to send newsletter to %s: %s", email_addr, exc)
                results.append((email_addr, "failed", str(exc)))

    if run_id:
        _record_deliveries(run_id, results)
