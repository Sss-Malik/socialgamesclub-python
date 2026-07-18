# Three-Backend Integration (yolo, cashfrenzy, cashmachine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port three working HTTP-API backends — `yolo` (id 13), `cashfrenzy` (id 14), `cashmachine` (id 15) — from the parallel `casino_automation` project into this service, taking the service from 12 to 15 backends.

**Architecture:** Copy 12 files verbatim from the source project, then apply a small mechanical patch that severs their coupling to a `LeaderboardReward` feature this project does not have. All three were built from the same template as `goldentreasure`, so their eight `action_*` functions already match this project's dispatcher contract. Wiring is three edits (two in `celery_app.py`, one in `docker-compose.yml`), exactly as commit `205024e` did for `goldentreasure`.

**Tech Stack:** Python 3.11, FastAPI, Celery (Redis broker), SQLAlchemy + PyMySQL, `requests` + `urllib3`, pytest.

## Global Constraints

- **Source of truth:** `/Applications/development/python/casino_automation` at commit `0aaf98b`. Never edit the source project.
- **Zero shared-code changes.** `common/utils/db_actions.py`, `models.py`, `db.py`, `api/` and all 12 existing backends must be byte-identical at the end of this plan. Verify with `git status`.
- **No new dependencies.** `requests==2.32.3`, `urllib3==2.4.0`, `redis==6.2.0` are already in `requirements.txt`. Do not modify it.
- **BACKEND_ID values are fixed:** yolo 13, cashfrenzy 14, cashmachine 15. Rows already exist in `backend_games` with these ids. Do not change them.
- **No live vendor calls.** The vendors are IP-whitelisted to the production Elastic IP and credentials live in the production DB. Every test in this plan is offline.
- **macOS `sed` requires `-i ''`** for in-place editing. Also: `head` on this machine is the Perl LWP `head`, not coreutils — use `sed -n '1,20p'` instead.
- **Working branch:** `feat/yolo-cashfrenzy-cashmachine` (already created, spec already committed as `42cbb5e`).
- All commands run from `/Applications/development/python/socialgamesclub`.
- The existing 12 tests must pass at every commit.

---

### Task 1: Copy the three backends verbatim

Copy first, patch second, in separate commits. That way the patch commits show exactly what diverged from the working source, which is the whole audit value of a verbatim port. The code is intentionally broken at the end of this task — Task 2 fixes it.

**Files:**
- Create: `backends/yolo/{__init__.py,config.py,api_client.py,automation.py}`
- Create: `backends/yolo/utils/{__init__.py,credentials.py}`
- Create: `backends/cashfrenzy/{__init__.py,config.py,api_client.py,automation.py}`
- Create: `backends/cashfrenzy/utils/{__init__.py,credentials.py}`
- Create: `backends/cashmachine/{__init__.py,config.py,api_client.py,automation.py}`
- Create: `backends/cashmachine/utils/{__init__.py,credentials.py}`

**Interfaces:**
- Consumes: nothing.
- Produces: `backends.<name>.automation` modules exposing the eight actions (`action_create_account`, `action_create_account_user`, `action_read_backend`, `action_read_account`, `action_recharge_account`, `action_withdraw_account`, `action_freeplay_account`, `action_reset_password`); `backends.<name>.api_client.build_client_from_backend(backend, logger)` returning `YoloClient` / `CashFrenzyClient` / `CashMachineClient`.

- [ ] **Step 1: Copy the three backend directories, excluding bytecode**

```bash
SRC=/Applications/development/python/casino_automation/backends
for b in yolo cashfrenzy cashmachine; do
  rsync -a --exclude='__pycache__' "$SRC/$b/" "backends/$b/"
done
```

- [ ] **Step 2: Create the one missing `__init__.py`**

`cashmachine/utils/` has no `__init__.py` in the source. It still imports there (Python 3 treats it as a PEP 420 namespace package), but every other backend here ships one. Add it for consistency.

```bash
touch backends/cashmachine/utils/__init__.py
```

- [ ] **Step 3: Verify the copy is complete and faithful**

```bash
ls backends/yolo backends/yolo/utils backends/cashfrenzy backends/cashfrenzy/utils backends/cashmachine backends/cashmachine/utils
diff -r --exclude='__pycache__' /Applications/development/python/casino_automation/backends/yolo backends/yolo
```

Expected: all six directory listings show the files above; `diff -r` prints nothing (identical).

- [ ] **Step 4: Confirm no shared code was touched**

```bash
git status --short
```

Expected: only `backends/yolo/`, `backends/cashfrenzy/`, `backends/cashmachine/` appear as untracked. Nothing under `common/`, `api/`, `models.py`, `db.py`, `requirements.txt`.

- [ ] **Step 5: Commit**

```bash
git add backends/yolo backends/cashfrenzy backends/cashmachine
git commit -m "Copy yolo, cashfrenzy, cashmachine verbatim from casino_automation

Unmodified copies from casino_automation@0aaf98b so the adaptation
commits that follow show exactly what diverged. Not yet functional:
all three recharge paths still reference the LeaderboardReward feature
this project does not have."
```

---

### Task 2: Sever the LeaderboardReward coupling

The source project has a `LeaderboardReward` feature this project lacks. Each `automation.py` calls `restore_wallet_balance()` with a 5th argument and passes `restore_leaderboard_reward=` to `process_recharge_operation()`. This project's signatures are `restore_wallet_balance(wallet_id, restore_amount, order_id, coupon_code=None)` and a `process_recharge_operation` with no leaderboard kwargs, so **both raise `TypeError` on the recharge failure path** — the exact path that refunds a customer.

**Files:**
- Modify: `backends/yolo/automation.py:356,403,473,479`
- Modify: `backends/cashfrenzy/automation.py:437,476,522,527`
- Modify: `backends/cashmachine/automation.py:457,505,575,581`

**Interfaces:**
- Consumes: the modules from Task 1.
- Produces: `action_recharge_account(count, account_id, order_id, task_id, backend, wallet_id, amount_to_deduct, coupon_code=None, **_)` in all three modules — no `leaderboard_reward_id` parameter.

- [ ] **Step 1: Remove `leaderboard_reward_id` from the three signatures**

The signature line is identical and unique in all three files:

```bash
for b in yolo cashfrenzy cashmachine; do
  sed -i '' \
    's/^    wallet_id, amount_to_deduct, coupon_code=None, leaderboard_reward_id=None, \*\*_,$/    wallet_id, amount_to_deduct, coupon_code=None, **_,/' \
    "backends/$b/automation.py"
done
```

- [ ] **Step 2: Delete the `restore_leaderboard_reward` kwargs lines**

Each file has two, at different indentation levels. Deleting whole lines handles both — the kwarg pair always sits alone on its own line.

```bash
for b in yolo cashfrenzy cashmachine; do
  sed -i '' '/restore_leaderboard_reward=True/d' "backends/$b/automation.py"
done
```

- [ ] **Step 3: Drop the 5th argument from `restore_wallet_balance`**

```bash
for b in yolo cashfrenzy cashmachine; do
  sed -i '' \
    's/restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code, leaderboard_reward_id)/restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code)/' \
    "backends/$b/automation.py"
done
```

- [ ] **Step 4: Verify every trace is gone**

```bash
grep -rn "leaderboard" backends/yolo backends/cashfrenzy backends/cashmachine
```

Expected: **no output at all**, and exit code 1. If any line prints, a call site was missed — fix it before continuing.

- [ ] **Step 5: Verify the three modules import cleanly**

```bash
DB_USER=t DB_PASS=t DB_HOST=localhost DB_NAME=t APP_KEY=t \
  ./venv/bin/python -c "
import importlib
for b in ['yolo','cashfrenzy','cashmachine']:
    m = importlib.import_module(f'backends.{b}.automation')
    print(b, 'OK', m.BACKEND_ID)
"
```

Expected:
```
yolo OK 13
cashfrenzy OK 14
cashmachine OK 15
```

- [ ] **Step 6: Commit**

```bash
git add backends/yolo/automation.py backends/cashfrenzy/automation.py backends/cashmachine/automation.py
git commit -m "Strip LeaderboardReward coupling from the three ported backends

The source project restores a leaderboard reward alongside the wallet on
recharge failure; this project has no such feature. Left in place, both
restore_wallet_balance() (5 args vs 4) and process_recharge_operation()
(unknown kwargs) would raise TypeError on the recharge failure path --
the path that refunds the customer."
```

---

### Task 3: Drop the `backend_id` kwarg and blank the committed credentials

Two independent adjustments, both small, both reviewable together as "make the ported code match this project's conventions".

**The `backend_id` trap:** `backend_id=BACKEND_ID` appears roughly 20 times per file, and **almost all of them are correct** — `insert_log(...)` and `process_recharge_operation(...)` both legitimately take a `backend_id`. Only the two functions below must lose it. A blanket find-and-replace will silently break logging on all three backends.

The five sites are exactly:

| File | Call |
|---|---|
| `backends/yolo/automation.py` | `update_game_id_by_username(account_id, player_id, backend_id=BACKEND_ID)` |
| `backends/yolo/automation.py` | `update_password_by_username(username=account_id, new_password=password, backend_id=BACKEND_ID)` |
| `backends/cashfrenzy/automation.py` | `update_game_id_by_username(account_id, backend_account_id, backend_id=BACKEND_ID)` |
| `backends/cashfrenzy/automation.py` | `update_password_by_username(username=account_id, new_password=password, backend_id=BACKEND_ID)` |
| `backends/cashmachine/automation.py` | `update_password_by_username(username=account_id, new_password=password, backend_id=BACKEND_ID)` |

(`cashmachine` already calls `update_game_id_by_username` with two arguments — it needs no change.)

**Files:**
- Modify: `backends/yolo/automation.py`, `backends/cashfrenzy/automation.py`, `backends/cashmachine/automation.py`
- Modify: `backends/yolo/config.py:13-14`, `backends/cashfrenzy/config.py:13-14`

**Interfaces:**
- Consumes: modules from Task 2.
- Produces: configs whose `USERNAME`/`PASSWORD` are empty strings; `build_client_from_backend` therefore sources credentials from `backend_games.username` / `.password` and raises `ValueError` if the DB row is unpopulated.

- [ ] **Step 1: Record the current count of legitimate `backend_id=BACKEND_ID` uses**

```bash
grep -c "backend_id=BACKEND_ID" backends/yolo/automation.py backends/cashfrenzy/automation.py backends/cashmachine/automation.py
```

Expected (note the numbers — Step 3 checks them):
```
backends/yolo/automation.py:19
backends/cashfrenzy/automation.py:18
backends/cashmachine/automation.py:16
```

- [ ] **Step 2: Remove the kwarg from exactly those two function calls**

These two substitutions are anchored to the function name, so `insert_log` and `process_recharge_operation` are untouched.

```bash
for b in yolo cashfrenzy cashmachine; do
  sed -i '' \
    -e 's/\(update_game_id_by_username([^)]*\), backend_id=BACKEND_ID)/\1)/' \
    -e 's/\(update_password_by_username([^)]*\), backend_id=BACKEND_ID)/\1)/' \
    "backends/$b/automation.py"
done
```

- [ ] **Step 3: Verify only the five intended sites changed**

```bash
grep -n "update_game_id_by_username(\|update_password_by_username(" backends/yolo/automation.py backends/cashfrenzy/automation.py backends/cashmachine/automation.py
grep -c "backend_id=BACKEND_ID" backends/yolo/automation.py backends/cashfrenzy/automation.py backends/cashmachine/automation.py
```

Expected: no `update_*_by_username` line contains `backend_id`, and the counts have dropped by exactly the number of sites in each file (yolo 2, cashfrenzy 2, cashmachine 1) — leaving yolo `17`, cashfrenzy `16`, cashmachine `15`. Any other drop means the substitution hit an `insert_log` or `process_recharge_operation` call and must be reverted.

- [ ] **Step 4: Blank the committed credentials in yolo**

In `backends/yolo/config.py`, replace:

```python
# — Login credentials (fallback when backend_games columns are unset) —
USERNAME = "webyolo1"
PASSWORD = "Web@@1122"
```

with:

```python
# — Login credentials —
# Real credentials live in the Laravel-managed `backend_games` row and reach
# the client via build_client_from_backend(), which prefers backend.username /
# backend.password. These are empty fallbacks — never commit live credentials.
USERNAME = ""
PASSWORD = ""
```

- [ ] **Step 5: Blank the committed credentials in cashfrenzy**

In `backends/cashfrenzy/config.py`, replace:

```python
# — Login credentials (fallback when backend_games columns are unset) —
USERNAME = "webcf852"
PASSWORD = "Zaeem@123"
```

with:

```python
# — Login credentials —
# Real credentials live in the Laravel-managed `backend_games` row and reach
# the client via build_client_from_backend(), which prefers backend.username /
# backend.password. These are empty fallbacks — never commit live credentials.
USERNAME = ""
PASSWORD = ""
```

- [ ] **Step 6: Verify no live credentials remain**

```bash
grep -n "USERNAME\s*=\|PASSWORD\s*=" backends/yolo/config.py backends/cashfrenzy/config.py backends/cashmachine/config.py
```

Expected: every line assigns `""`.

- [ ] **Step 7: Re-verify the modules still import**

```bash
DB_USER=t DB_PASS=t DB_HOST=localhost DB_NAME=t APP_KEY=t \
  ./venv/bin/python -c "
import importlib
for b in ['yolo','cashfrenzy','cashmachine']:
    importlib.import_module(f'backends.{b}.automation')
print('all three import OK')
"
```

Expected: `all three import OK`

- [ ] **Step 8: Commit**

```bash
git add backends/yolo backends/cashfrenzy backends/cashmachine
git commit -m "Match this project's db_actions signatures and credential handling

update_game_id_by_username/update_password_by_username take no backend_id
here. Only those two call sites lose the kwarg -- insert_log and
process_recharge_operation legitimately take backend_id and keep it.

Also blanks the live agent credentials the source committed for yolo and
cashfrenzy; both now read from backend_games like cashmachine already did."
```

---

### Task 4: Recharge failure-path test

This is the test that actually guards this port. The dispatch contract test in Task 5 cannot catch the failure this work risks: all three actions end in `**_` and will bind any payload. The hazard is a `TypeError` raised *inside* the function body, on the failure path, which is exactly where the customer's refund lives. A missed call site strands real money and surfaces only in production, on a failed recharge.

The recorder binds against the **real** `restore_wallet_balance` signature, so a leftover 5th argument fails loudly instead of being absorbed by a permissive stub.

**Files:**
- Create: `tests/test_new_backend_recharge_restore.py`

**Interfaces:**
- Consumes: `action_recharge_account` from all three modules (Task 2 signature).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test**

```python
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

    # Must not raise: the except block owns the refund.
    module.action_recharge_account(
        count=50,
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
```

- [ ] **Step 2: Run the test**

```bash
./venv/bin/python -m pytest tests/test_new_backend_recharge_restore.py -v
```

Expected: 6 passed (3 backends × 2 tests).

- [ ] **Step 3: Prove the test can actually fail**

A test that passes the first time proves nothing. Temporarily reintroduce the bug in one backend and confirm the test catches it:

```bash
sed -i '' \
  's/restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code)/restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code, 99)/' \
  backends/yolo/automation.py
./venv/bin/python -m pytest tests/test_new_backend_recharge_restore.py -v
```

Expected: `test_recharge_failure_refunds_wallet[yolo]` **FAILS**. The `cashfrenzy` and `cashmachine` cases still pass.

- [ ] **Step 4: Revert the deliberate break and confirm green again**

```bash
git checkout backends/yolo/automation.py
./venv/bin/python -m pytest tests/test_new_backend_recharge_restore.py -v
```

Expected: 6 passed. Confirm `git status --short` shows no modification to `backends/yolo/automation.py`.

- [ ] **Step 5: Run the whole suite**

```bash
./venv/bin/python -m pytest -q
```

Expected: `18 passed` (12 existing + 6 new).

- [ ] **Step 6: Commit**

```bash
git add tests/test_new_backend_recharge_restore.py
git commit -m "Test that the ported backends refund with this project's signature

Guards the failure path specifically: api/main.py debits before enqueueing,
so a TypeError in the except block strands the customer's money. The
recorder binds against the real restore_wallet_balance signature, so a
leftover leaderboard argument fails instead of being absorbed by the stub."
```

---

### Task 5: Dispatch contract test

Ported from the source project's `tests/test_dispatch_contract.py`, adapted to this project's payload (no `leaderboard_reward_id`). Covers all 15 backends and any future one, with no hardcoded list.

**Files:**
- Create: `tests/test_dispatch_contract.py`

**Interfaces:**
- Consumes: every `backends/*/automation*.py` module.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test**

```python
"""Dispatch contract test for action_recharge_account across all backends.

api/dispatcher.py invokes every backend with a fixed kwarg shape:

    fn(backend=backend, **kwargs)

where kwargs is api/main.py's recharge_account queue_kwargs (account_id,
count, order_id, wallet_id, amount_to_deduct, coupon_code) plus task_id,
which api/tasks.py's invoke_action adds before dispatching.

The seven Playwright backends declare strict signatures with no **kwargs
catch-all. If the payload ever gains or renames a field one of them does not
accept, the call raises TypeError at the dispatcher's call boundary -- BEFORE
the function's own try block -- so its except path (which calls
restore_wallet_balance) never runs. api/main.py has already debited the
customer's wallet by then, leaving the money stranded with nothing to
restore it.

bind_partial rather than bind: the Playwright backends take `page` as their
first parameter, injected by the with_persistent_browser decorator at call
time and never present in the dispatcher's kwargs. A plain bind() would fail
for all seven with "missing page" -- a false positive. bind_partial tolerates
that while still raising TypeError for the thing that matters: a payload key
the signature does not accept.
"""
import glob
import importlib
import inspect
import os

import pytest

_BACKENDS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backends"
)


def _discover_backend_modules():
    """Dotted module paths for every backends/*/automation*.py file.

    Globbed, never hardcoded, so a 16th backend is covered automatically.
    """
    pattern = os.path.join(_BACKENDS_ROOT, "*", "automation*.py")
    dotted = []
    for path in sorted(glob.glob(pattern)):
        pkg = os.path.basename(os.path.dirname(path))
        mod_name = os.path.splitext(os.path.basename(path))[0]
        dotted.append(f"backends.{pkg}.{mod_name}")
    return dotted


DISCOVERED_MODULES = _discover_backend_modules()

# The real payload: api/main.py's queue_kwargs, plus task_id (added by
# api/tasks.py's invoke_action) and backend (added by api/dispatcher.py).
DISPATCH_PAYLOAD = {
    "account_id": "a",
    "count": 1,
    "order_id": "o",
    "wallet_id": 1,
    "amount_to_deduct": 1,
    "coupon_code": None,
    "task_id": "t",
    "backend": "x",
}


def test_discovery_found_backend_modules():
    """Guard against the glob silently matching nothing, which would make every
    parametrized case below vanish. Zero collected tests must never be mistaken
    for a passing run."""
    assert len(DISCOVERED_MODULES) >= 15, (
        f"expected at least 15 backends/*/automation*.py modules, found "
        f"{len(DISCOVERED_MODULES)}: {DISCOVERED_MODULES}"
    )


@pytest.mark.parametrize("module_path", DISCOVERED_MODULES)
def test_action_recharge_account_binds_dispatch_payload(module_path):
    """Every backend's action_recharge_account must accept the exact kwargs
    api/dispatcher.py calls it with, or a TypeError at the call boundary
    strands the wallet deduction with no restore path."""
    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        # A visible skip, never a silent pass: the Playwright backends launch
        # Chromium at import via common/utils/playwright_pool.py and will skip
        # on a machine without browsers installed.
        pytest.skip(f"{module_path}: cannot import ({type(e).__name__}: {e})")

    fn = getattr(module, "action_recharge_account", None)
    if fn is None:
        pytest.fail(f"{module_path}: has no action_recharge_account")

    try:
        inspect.signature(fn).bind_partial(**DISPATCH_PAYLOAD)
    except TypeError as e:
        pytest.fail(
            f"{module_path}.action_recharge_account cannot bind the real "
            f"dispatch payload -- this TypeError would be raised at the "
            f"dispatcher's call boundary, before the function's own try "
            f"block, stranding the wallet deduction with no restore: {e}"
        )
```

- [ ] **Step 2: Run it and record which backends skip**

```bash
./venv/bin/python -m pytest tests/test_dispatch_contract.py -v -rs
```

Expected: 16 tests collected (1 discovery guard + 15 backends). The three new backends and the five existing HTTP backends must **PASS**. The seven Playwright backends will pass or skip depending on whether Playwright and Chromium are installed locally — a skip is acceptable and will be reported by `-rs`, but a **FAIL** is not.

- [ ] **Step 3: Confirm the discovery guard works**

```bash
./venv/bin/python -m pytest tests/test_dispatch_contract.py::test_discovery_found_backend_modules -v
```

Expected: PASS. If it fails, the glob found fewer than 15 modules and every other case in this file is silently vacuous.

- [ ] **Step 4: Run the whole suite**

```bash
./venv/bin/python -m pytest -q
```

Expected: `34 passed` (12 existing + 6 from Task 4 + 16 here), possibly with some of the 16 reported as skipped.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dispatch_contract.py
git commit -m "Add dispatch contract test across all backends

Asserts every action_recharge_account can bind the dispatcher's real
payload. A mismatch raises TypeError at the call boundary, before the
function's own except path can refund the already-debited wallet.
Discovers backends by glob, so future ones are covered automatically."
```

---

### Task 6: Wire the three backends into Celery and Docker

Without this, tasks enqueue onto queues no worker consumes and sit forever. Three edits, mirroring commit `205024e` for `goldentreasure`.

**Files:**
- Modify: `celery_app.py:38-52` (task_queues), `celery_app.py:57-76` (task_routes)
- Modify: `docker-compose.yml:62` (worker `-Q` list)

**Interfaces:**
- Consumes: the three `backends.<name>.automation` modules.
- Produces: three live Celery queues named `yolo`, `cashfrenzy`, `cashmachine`.

- [ ] **Step 1: Add the three queues**

In `celery_app.py`, in the `task_queues` tuple, after the `goldentreasure` line:

```python
    Queue("goldentreasure", Exchange("goldentreasure"), routing_key="goldentreasure"),
    Queue("yolo",       Exchange("yolo"),       routing_key="yolo"),
    Queue("cashfrenzy", Exchange("cashfrenzy"), routing_key="cashfrenzy"),
    Queue("cashmachine",Exchange("cashmachine"),routing_key="cashmachine"),
)
```

- [ ] **Step 2: Add the three routes**

In `celery_app.py`, in the `task_routes` dict, after the `goldentreasure` line:

```python
    "backends.goldentreasure.automation.action_*": {"queue": "goldentreasure"},
    "backends.yolo.automation.action_*":        {"queue": "yolo"},
    "backends.cashfrenzy.automation.action_*":  {"queue": "cashfrenzy"},
    "backends.cashmachine.automation.action_*": {"queue": "cashmachine"},
}
```

- [ ] **Step 3: Add the three queues to the worker**

In `docker-compose.yml`, replace the `-Q` line:

```yaml
        -Q default,juwa,juwa2,orionstars,gameroom,gamevault,firekirin,ultrapanda,pandamaster,vblink,river,milkyway,goldentreasure
```

with:

```yaml
        -Q default,juwa,juwa2,orionstars,gameroom,gamevault,firekirin,ultrapanda,pandamaster,vblink,river,milkyway,goldentreasure,yolo,cashfrenzy,cashmachine
```

- [ ] **Step 4: Verify Celery accepts the config and routes correctly**

```bash
DB_USER=t DB_PASS=t DB_HOST=localhost DB_NAME=t APP_KEY=t \
  ./venv/bin/python -c "
from celery_app import celery_app
names = sorted(q.name for q in celery_app.conf.task_queues)
print('queues:', len(names))
for b in ['yolo','cashfrenzy','cashmachine']:
    assert b in names, f'{b} queue missing'
    key = f'backends.{b}.automation.action_*'
    assert celery_app.conf.task_routes[key] == {'queue': b}, f'{b} route wrong'
print('all three wired OK')
"
```

Expected:
```
queues: 16
all three wired OK
```

- [ ] **Step 5: Verify compose is still valid and lists all 16 queues**

```bash
grep -o '\-Q [a-z0-9,]*' docker-compose.yml
```

Expected: one line ending `...,goldentreasure,yolo,cashfrenzy,cashmachine`. Confirm it contains 16 comma-separated names (`default` plus 15 backends).

- [ ] **Step 6: Full verification**

```bash
./venv/bin/python -m pytest -q
git status --short
git diff --stat main -- common/ api/ models.py db.py requirements.txt
```

Expected: suite green; `git status` clean; the `git diff --stat` prints **nothing**, proving zero shared-code changes as required by the Global Constraints.

- [ ] **Step 7: Commit**

```bash
git add celery_app.py docker-compose.yml
git commit -m "Wire yolo, cashfrenzy and cashmachine into Celery and the worker

Adds the three queues and routes, and subscribes the worker to them.
Without this, tasks enqueue onto queues nothing consumes."
```

---

## Deployment note (not a code task)

This is deploy-time information for whoever ships the branch, not a step to execute now.

The three `backend_games` rows already exist with ids 13, 14 and 15. Because Task 3 blanks the config credential fallbacks, each row's `username` and `password` columns **must** be populated or the client raises `ValueError: <N>Client requires base_url, username, and password.` on first use. Each row also needs `api_base_url` and `accounts_creation_pd` set, `name` exactly matching `config.BACKEND_NAME`, and `deleted_at` NULL.

Expected `api_base_url` values, from the source configs:

| Backend | api_base_url |
|---|---|
| yolo | `https://agent.yolo-777.com` |
| cashfrenzy | `https://agentserver.cashfrenzy777.com` |
| cashmachine | `https://agentserver.cashmachine777.com` |

All three are new outbound destinations from the production Elastic IP (16.144.166.215). If any of these vendors IP-whitelists — as juwa, juwa2 and gamevault do — that whitelisting must be arranged out-of-band with the operator before the backend will work in production. Verify after deploy by running `read-backend` against each and checking for an auth/IP error rather than assuming success.

Deploying: the box live-mounts the repo, so `git pull && docker compose restart` picks the change up. The worker `-Q` change requires a worker restart specifically, not just a code reload.
