"""Tests for the newsletter_deliveries model (per-run, per-subscriber send tracking)."""

from sqlalchemy import inspect, select

from src.db import get_session


def test_deliveries_table_has_expected_columns(db):
    from src.models import NewsletterDelivery  # noqa: F401

    cols = {c["name"] for c in inspect(db).get_columns("newsletter_deliveries")}
    assert cols == {"id", "run_id", "email", "status", "error", "sent_at"}


def test_records_a_delivery(db):
    from src.models import NewsletterDelivery

    with get_session() as session:
        session.add(
            NewsletterDelivery(run_id="run-1", email="a@example.com", status="sent")
        )

    with get_session() as session:
        row = session.scalars(
            select(NewsletterDelivery).where(NewsletterDelivery.run_id == "run-1")
        ).one()
        assert row.email == "a@example.com"
        assert row.status == "sent"
        assert row.error is None
        assert row.sent_at is not None
