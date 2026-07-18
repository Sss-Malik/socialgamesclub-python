"""The three ported backends must refund the wallet using THIS project's
restore_wallet_balance signature.

Each action_recharge_account ends in a **_ catch-all, so a signature-level
contract test (tests/test_dispatch_contract.py) cannot see a bad call *inside*
the function body. The dangerous version of that bug is on the failure path:
api/main.py has already debited the customer before enqueueing, so if the
except block raises TypeError instead of refunding, the money is stranded.

These backends were ported from a project whose restore_wallet_balance takes a
fifth leaderboard_reward_id argument. This test fails loudly if any of that
survived.
"""
import importlib
import inspect
import logging

import pytest

import common.utils.db_actions as db_actions

NEW_BACKENDS = ["yolo", "cashfrenzy", "cashmachine"]


class _ExplodingClient:
    """Any method call raises, simulating a vendor/network failure.

    db_session_id is a real attribute (not routed through __getattr__) so
    cashmachine's _dec_active_tasks() short-circuits instead of calling the
    real decrement_active_tasks_count() against the database.
    """

    db_session_id = None

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise RuntimeError(f"simulated vendor failure in {name}()")

        return _boom


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _install_stubs(monkeypatch, module, calls):
    real_sig = inspect.signature(db_actions.restore_wallet_balance)

    def _recording_restore(*args, **kwargs):
        # Bind against the REAL signature: a leftover 5th positional argument
        # raises TypeError here rather than being silently swallowed.
        real_sig.bind(*args, **kwargs)
        calls.append((args, kwargs))

    backend_row = _Row(id=99, name="stub-backend")
    account_row = _Row(id=7, username="userXX1", user=_Row(bonus_received=False))

    monkeypatch.setattr(
        module, "get_backend_and_account", lambda *a, **k: (backend_row, account_row)
    )
    monkeypatch.setattr(module, "ensure_directories", lambda *a, **k: None)
    monkeypatch.setattr(
        module, "get_backend_logger", lambda *a, **k: logging.getLogger("test")
    )
    monkeypatch.setattr(
        module, "build_client_from_backend", lambda *a, **k: _ExplodingClient()
    )
    monkeypatch.setattr(module, "restore_wallet_balance", _recording_restore)
    monkeypatch.setattr(module, "insert_log", lambda *a, **k: None)
    monkeypatch.setattr(
        module, "insert_log_and_update_automation_result", lambda *a, **k: None
    )
    monkeypatch.setattr(module, "send_email", lambda *a, **k: None)


@pytest.mark.parametrize("backend_name", NEW_BACKENDS)
def test_recharge_failure_refunds_wallet(backend_name, monkeypatch):
    module = importlib.import_module(f"backends.{backend_name}.automation")
    calls = []
    _install_stubs(monkeypatch, module, calls)

    # count and amount_to_deduct are deliberately distinct: if a regression
    # swapped them (refunding the recharge count instead of the amount
    # debited), this assertion must catch it.
    # Must not raise: the except block owns the refund.
    module.action_recharge_account(
        count=999,
        account_id="userXX1",
        order_id="ORD-1",
        task_id="task-1",
        backend=backend_name,
        wallet_id=1,
        amount_to_deduct=50,
        coupon_code=None,
    )

    assert len(calls) == 1, (
        f"{backend_name}: expected exactly one refund on the failure path, "
        f"got {len(calls)}"
    )
    args, kwargs = calls[0]
    # The refund must equal amount_to_deduct (the amount actually debited),
    # never count (999) -- restore_wallet_balance(wallet_id, amount, order_id, coupon_code).
    assert args == (1, 50, "ORD-1", None)
    assert kwargs == {}


@pytest.mark.parametrize("backend_name", NEW_BACKENDS)
def test_recharge_signature_rejects_leaderboard_kwarg(backend_name):
    """The leaderboard parameter must be gone from the signature, not merely
    unused -- otherwise a caller could still pass it and reach a body that no
    longer handles it."""
    module = importlib.import_module(f"backends.{backend_name}.automation")
    params = inspect.signature(module.action_recharge_account).parameters
    assert "leaderboard_reward_id" not in params
