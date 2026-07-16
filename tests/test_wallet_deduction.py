"""
deduct_wallet_balance is the point where a customer is actually billed.

Every failure here is a money failure: if it fails quietly the caller carries on
and recharges the player's account for free, and if it debits without the task
being enqueued the customer pays for nothing. So the contract under test is that
it either debits exactly once or raises.
"""

import logging
from decimal import Decimal

import pytest

from common.utils.db_actions import (
    InsufficientBalance,
    WalletNotFound,
    deduct_wallet_balance,
    restore_wallet_balance,
)
from conftest import balance_of


def test_debits_the_wallet(session_factory, wallet):
    wallet_id = wallet(balance=100)

    deduct_wallet_balance(wallet_id=wallet_id, deduct_amount=30)

    assert balance_of(session_factory, wallet_id) == Decimal("70")


def test_allows_spending_the_exact_balance(session_factory, wallet):
    wallet_id = wallet(balance=50)

    deduct_wallet_balance(wallet_id=wallet_id, deduct_amount=50)

    assert balance_of(session_factory, wallet_id) == Decimal("0")


def test_raises_when_the_wallet_does_not_exist(session_factory):
    with pytest.raises(WalletNotFound):
        deduct_wallet_balance(wallet_id=999, deduct_amount=50)


def test_raises_when_the_balance_is_insufficient(session_factory, wallet):
    wallet_id = wallet(balance=10)

    with pytest.raises(InsufficientBalance):
        deduct_wallet_balance(wallet_id=wallet_id, deduct_amount=50)

    assert balance_of(session_factory, wallet_id) == Decimal("10")


def test_never_leaves_a_negative_balance(session_factory, wallet):
    wallet_id = wallet(balance=10)

    with pytest.raises(InsufficientBalance):
        deduct_wallet_balance(wallet_id=wallet_id, deduct_amount=11)

    assert balance_of(session_factory, wallet_id) >= 0


def test_refund_failures_are_logged_rather_than_swallowed(
    session_factory, wallet, caplog
):
    """
    restore_wallet_balance deliberately does not raise — twelve backend failure
    paths call it while already handling an error. But a refund that fails in
    silence leaves the customer out of pocket with nothing in the logs to show
    for it, so it has to at least say so.

    wallet_detail is absent from this schema (it needs MySQL-only types), so the
    refund hits a genuine database error part-way through.
    """
    wallet_id = wallet(balance=50)

    with caplog.at_level(logging.ERROR):
        restore_wallet_balance(wallet_id=wallet_id, restore_amount=50, order_id="ord-1")

    assert "ord-1" in caplog.text


def test_propagates_database_errors_instead_of_swallowing_them(
    session_factory, wallet, monkeypatch
):
    """A swallowed commit error bills nobody but lets the recharge proceed."""
    wallet_id = wallet(balance=100)

    import common.utils.db_actions as db_actions

    def exploding_factory():
        db = session_factory()
        monkeypatch.setattr(
            db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db is down"))
        )
        return db

    monkeypatch.setattr(db_actions, "SessionLocal", exploding_factory)

    with pytest.raises(RuntimeError, match="db is down"):
        deduct_wallet_balance(wallet_id=wallet_id, deduct_amount=30)
