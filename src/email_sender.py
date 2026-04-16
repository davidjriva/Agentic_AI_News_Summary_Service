from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from config import Config

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, config: Config) -> None:
        self._config = config

    def send_newsletter(
        self,
        html_content: str,
        plaintext_content: str,
        subject: str,
    ) -> bool:
        return self._send(
            html_content=html_content,
            plaintext_content=plaintext_content,
            subject=subject,
            recipients=self._config.recipient_emails,
        )

    def send_test(self, html_content: str) -> bool:
        if not self._config.recipient_emails:
            logger.warning("No recipient emails configured; cannot send test email")
            return False

        return self._send(
            html_content=html_content,
            plaintext_content="",
            subject="[TEST] " + self._config.newsletter_title,
            recipients=[self._config.recipient_emails[0]],
        )

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _build_message(
        self,
        html_content: str,
        plaintext_content: str,
        subject: str,
        recipients: List[str],
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._config.smtp_from or self._config.smtp_user
        msg["To"] = ", ".join(recipients)

        if plaintext_content:
            msg.attach(MIMEText(plaintext_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))
        return msg

    def _send(
        self,
        html_content: str,
        plaintext_content: str,
        subject: str,
        recipients: List[str],
    ) -> bool:
        if not recipients:
            logger.warning("send called with empty recipient list; skipping")
            return False

        msg = self._build_message(html_content, plaintext_content, subject, recipients)

        try:
            with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self._config.smtp_user, self._config.smtp_password)
                smtp.sendmail(
                    from_addr=msg["From"],
                    to_addrs=recipients,
                    msg=msg.as_string(),
                )
            logger.info(
                "Newsletter sent successfully to %d recipient(s): %s",
                len(recipients),
                ", ".join(recipients),
            )
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP authentication failed for user '%s'", self._config.smtp_user
            )
        except smtplib.SMTPConnectError:
            logger.error(
                "Could not connect to SMTP server %s:%d",
                self._config.smtp_host,
                self._config.smtp_port,
            )
        except smtplib.SMTPException:
            logger.error("SMTP error while sending newsletter", exc_info=True)
        except OSError:
            logger.error(
                "Network error contacting SMTP server %s:%d",
                self._config.smtp_host,
                self._config.smtp_port,
                exc_info=True,
            )
        return False
