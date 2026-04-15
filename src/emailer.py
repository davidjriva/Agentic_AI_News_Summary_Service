"""Email delivery module for the Agentic AI News Summary Service."""

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import RECIPIENTS


def send_newsletter(html: str, plain_text: str, run_time: datetime) -> None:
    """Send the newsletter email to all recipients via Gmail SMTP.

    Args:
        html: HTML body of the newsletter.
        plain_text: Plain-text fallback body.
        run_time: The datetime at which this newsletter run was initiated.

    Raises:
        smtplib.SMTPException: On any SMTP-level failure.
        RuntimeError: If credentials are missing from the environment.
    """
    sender = os.getenv("GMAIL_SENDER")
    password = os.getenv("GMAIL_APP_PASSWORD")

    am_pm = "AM" if run_time.hour < 12 else "PM"
    subject = f"Agentic AI Digest - {run_time.strftime('%B %d, %Y')} {am_pm}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(RECIPIENTS)

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.sendmail(sender, RECIPIENTS, msg.as_string())
    except smtplib.SMTPException as exc:
        raise smtplib.SMTPException(
            f"Failed to send newsletter '{subject}' to {RECIPIENTS}: {exc}"
        ) from exc
