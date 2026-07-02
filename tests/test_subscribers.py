"""Tests for the subscribers data model."""

from sqlalchemy import inspect, select

from src.db import get_session


def test_subscribers_table_exists_with_expected_columns(db):
    from src.models import Subscriber  # noqa: F401

    cols = {c["name"] for c in inspect(db).get_columns("subscribers")}
    assert cols == {
        "id", "email", "status", "confirm_token", "unsubscribe_token",
        "subscribed_at", "confirmed_at", "unsubscribed_at",
    }


def test_persists_and_filters_confirmed_subscribers(db):
    from src.models import Subscriber

    with get_session() as session:
        session.add_all([
            Subscriber(email="confirmed@example.com", status="confirmed", unsubscribe_token="tok-c"),
            Subscriber(email="pending@example.com", status="pending", unsubscribe_token="tok-p"),
            Subscriber(email="gone@example.com", status="unsubscribed", unsubscribe_token="tok-u"),
        ])

    with get_session() as session:
        confirmed = session.scalars(
            select(Subscriber.email).where(Subscriber.status == "confirmed")
        ).all()

    assert confirmed == ["confirmed@example.com"]


def test_status_defaults_to_pending(db):
    from src.models import Subscriber

    with get_session() as session:
        session.add(Subscriber(email="new@example.com", unsubscribe_token="tok-n"))

    with get_session() as session:
        row = session.scalars(
            select(Subscriber).where(Subscriber.email == "new@example.com")
        ).one()
        assert row.status == "pending"
        assert row.subscribed_at is not None
