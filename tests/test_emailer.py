"""Tests for src/emailer.py — per-subscriber newsletter delivery."""

import email
import smtplib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src import config as _cfg
from src.db import get_session
from src.models import NewsletterDelivery, Subscriber


def _deliveries(run_id):
    with get_session() as session:
        return {
            (d.email, d.status, d.error)
            for d in session.scalars(
                select(NewsletterDelivery).where(NewsletterDelivery.run_id == run_id)
            ).all()
        }

HTML = "<html><body><h1>Digest</h1><footer>%%UNSUBSCRIBE_URL%%</footer></body></html>"
PLAIN = "Digest - unsubscribe: %%UNSUBSCRIBE_URL%%"
RUN_AM = datetime(2026, 4, 14, 9, 30, 0)
RUN_PM = datetime(2026, 4, 14, 15, 0, 0)


def _smtp_mock():
    inst = MagicMock()
    inst.__enter__ = MagicMock(return_value=inst)
    inst.__exit__ = MagicMock(return_value=False)
    cls = MagicMock(return_value=inst)
    return cls, inst


def _seed(*subs):
    with get_session() as session:
        session.add_all(subs)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("GMAIL_SENDER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setattr(_cfg, "PORTFOLIO_BASE_URL", "https://portfolio.test")


def test_only_confirmed_subscribers_receive_mail(db, env):
    _seed(
        Subscriber(email="c1@example.com", status="confirmed", unsubscribe_token="t1"),
        Subscriber(email="c2@example.com", status="confirmed", unsubscribe_token="t2"),
        Subscriber(email="pending@example.com", status="pending", unsubscribe_token="tp"),
        Subscriber(email="gone@example.com", status="unsubscribed", unsubscribe_token="tu"),
    )
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM)

    assert inst.sendmail.call_count == 2
    recipients = {addr for c in inst.sendmail.call_args_list for addr in c.args[1]}
    assert recipients == {"c1@example.com", "c2@example.com"}


def test_each_message_is_single_recipient_with_personal_unsubscribe(db, env):
    _seed(Subscriber(email="c@example.com", status="confirmed", unsubscribe_token="abc123"))
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM)

    raw = inst.sendmail.call_args.args[2]
    msg = email.message_from_string(raw)
    expected = "https://portfolio.test/newsletter/unsubscribe?token=abc123"

    assert msg["To"] == "c@example.com"             # one recipient per message
    assert expected in raw                           # sentinel substituted in body
    assert "%%UNSUBSCRIBE_URL%%" not in raw
    assert msg["List-Unsubscribe"] == f"<{expected}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_message_is_multipart_alternative_with_both_parts(db, env):
    _seed(Subscriber(email="c@example.com", status="confirmed", unsubscribe_token="t"))
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM)

    msg = email.message_from_string(inst.sendmail.call_args.args[2])
    assert msg.get_content_type() == "multipart/alternative"
    types = [p.get_content_type() for p in msg.get_payload()]
    assert "text/plain" in types and "text/html" in types


def test_subject_contains_date_and_meridiem(db, env):
    _seed(Subscriber(email="c@example.com", status="confirmed", unsubscribe_token="t"))
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_PM)

    subject = email.message_from_string(inst.sendmail.call_args.args[2])["Subject"]
    assert "April 14, 2026" in subject
    assert "PM" in subject and "AM" not in subject


def test_per_recipient_failure_does_not_abort_batch(db, env):
    _seed(
        Subscriber(email="a@example.com", status="confirmed", unsubscribe_token="ta"),
        Subscriber(email="b@example.com", status="confirmed", unsubscribe_token="tb"),
    )
    cls, inst = _smtp_mock()
    inst.sendmail.side_effect = [smtplib.SMTPException("boom"), None]
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM)  # must not raise

    assert inst.sendmail.call_count == 2  # second still attempted


def test_no_confirmed_subscribers_opens_no_connection(db, env):
    _seed(Subscriber(email="pending@example.com", status="pending", unsubscribe_token="tp"))
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM)

    cls.assert_not_called()
    inst.sendmail.assert_not_called()


def test_records_a_delivery_per_confirmed_subscriber(db, env):
    _seed(
        Subscriber(email="a@example.com", status="confirmed", unsubscribe_token="ta"),
        Subscriber(email="b@example.com", status="confirmed", unsubscribe_token="tb"),
    )
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM, run_id="run-x")

    assert _deliveries("run-x") == {
        ("a@example.com", "sent", None),
        ("b@example.com", "sent", None),
    }


def test_failed_send_recorded_as_failed_with_error(db, env):
    _seed(
        Subscriber(email="a@example.com", status="confirmed", unsubscribe_token="ta"),
        Subscriber(email="b@example.com", status="confirmed", unsubscribe_token="tb"),
    )
    cls, inst = _smtp_mock()
    inst.sendmail.side_effect = [smtplib.SMTPException("boom"), None]
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM, run_id="run-y")

    rows = _deliveries("run-y")
    assert sorted(status for _, status, _ in rows) == ["failed", "sent"]
    failed = next(r for r in rows if r[1] == "failed")
    assert "boom" in failed[2]


def test_delivery_recording_is_idempotent(db, env):
    _seed(Subscriber(email="a@example.com", status="confirmed", unsubscribe_token="ta"))
    cls, inst = _smtp_mock()
    with patch("smtplib.SMTP", cls):
        from src.emailer import send_newsletter
        send_newsletter(HTML, PLAIN, RUN_AM, run_id="run-z")
        send_newsletter(HTML, PLAIN, RUN_AM, run_id="run-z")  # re-run same issue

    assert _deliveries("run-z") == {("a@example.com", "sent", None)}  # still one row
