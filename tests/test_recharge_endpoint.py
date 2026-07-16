"""
The recharge endpoint bills the customer before it enqueues the work.

That ordering is deliberate — it stops a user spending a balance they no longer
have — but it means every failure between the debit and a successfully queued
task has to hand the money back, or the customer pays for a recharge that never
runs.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.main import app, require_user_token
from common.utils.db_actions import InsufficientBalance


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "API_DELAY_SECONDS", 0)
    app.dependency_overrides[require_user_token] = lambda: SimpleNamespace(
        id=1, wallet_id=7, balance_minor=100
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def happy_path(monkeypatch):
    """Stub out everything the endpoint touches except the code under test."""
    monkeypatch.setattr(
        main, "get_validated_backend_account", lambda *a, **k: SimpleNamespace(id=1)
    )
    monkeypatch.setattr(main, "get_order", lambda *a, **k: SimpleNamespace(id="ord-1"))
    monkeypatch.setattr(main, "deduct_wallet_balance", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_coupon_validity_and_return_amount", lambda *a, **k: 5)


REQUEST = {
    "backend": "juwa",
    "count": 50,
    "amount_to_deduct": 50,
    "account_id": "abc123",
    "coupon_code": "SAVE5",
}
HEADERS = {"x-order-id": "ord-1"}


def test_refunds_the_wallet_when_enqueueing_fails(client, happy_path, monkeypatch):
    restored = []
    monkeypatch.setattr(
        main,
        "restore_wallet_balance",
        lambda **kwargs: restored.append(kwargs),
    )

    def broker_is_down(**kwargs):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(main, "_enqueue_action", broker_is_down)

    response = client.post("/automation/recharge-account", json=REQUEST, headers=HEADERS)

    assert response.status_code == 503
    assert restored == [
        {
            "wallet_id": 7,
            "restore_amount": 50,
            "order_id": "ord-1",
            "coupon_code": "SAVE5",
        }
    ]


def test_does_not_refund_when_enqueueing_succeeds(client, happy_path, monkeypatch):
    restored = []
    monkeypatch.setattr(
        main, "restore_wallet_balance", lambda **kwargs: restored.append(kwargs)
    )
    monkeypatch.setattr(
        main, "_enqueue_action", lambda **kwargs: {"status": "scheduled", "task_id": "t1"}
    )

    response = client.post("/automation/recharge-account", json=REQUEST, headers=HEADERS)

    assert response.status_code == 200
    assert restored == []


def test_does_not_resurrect_a_coupon_it_never_consumed(client, happy_path, monkeypatch):
    """
    An already-used or expired coupon yields no bonus and is not consumed.
    Passing its code to the refund anyway would flip a spent coupon back to
    pending and let it be redeemed a second time.
    """
    restored = []
    monkeypatch.setattr(
        main, "restore_wallet_balance", lambda **kwargs: restored.append(kwargs)
    )
    monkeypatch.setattr(main, "check_coupon_validity_and_return_amount", lambda *a, **k: 0)

    def broker_is_down(**kwargs):
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(main, "_enqueue_action", broker_is_down)

    client.post("/automation/recharge-account", json=REQUEST, headers=HEADERS)

    assert restored[0]["coupon_code"] is None
    assert restored[0]["restore_amount"] == 50


def test_surfaces_a_lost_balance_race_as_a_client_error(client, happy_path, monkeypatch):
    """
    The pre-check reads the balance in an earlier session. When the locked
    re-read inside deduct_wallet_balance disagrees, that is the user being out
    of money, not a server fault.
    """

    def out_of_money(**kwargs):
        raise InsufficientBalance("wallet 7 holds 0, cannot cover 50")

    monkeypatch.setattr(main, "deduct_wallet_balance", out_of_money)

    response = client.post("/automation/recharge-account", json=REQUEST, headers=HEADERS)

    assert response.status_code == 400


def test_rejects_insufficient_balance_without_billing(client, happy_path, monkeypatch):
    """The pre-check must still reject before any debit is attempted."""
    debited = []
    monkeypatch.setattr(main, "deduct_wallet_balance", lambda **k: debited.append(k))
    app.dependency_overrides[require_user_token] = lambda: SimpleNamespace(
        id=1, wallet_id=7, balance_minor=10
    )

    response = client.post("/automation/recharge-account", json=REQUEST, headers=HEADERS)

    assert response.status_code == 400
    assert debited == []
