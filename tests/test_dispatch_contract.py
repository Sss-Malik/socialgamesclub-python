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

try:
    from playwright.sync_api import Error as PlaywrightError
except ImportError:
    # playwright itself is not installed on this machine, so its Error type
    # can't be imported either. Use a dummy class that nothing ever raises;
    # the module-not-found case is instead handled by the ImportError branch
    # below (matched by module name), so this sentinel never needs to fire.
    class PlaywrightError(Exception):
        pass

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
    except PlaywrightError as e:
        # A visible skip, never a silent pass: the Playwright backends launch
        # Chromium at import via common/utils/playwright_pool.py, and this is
        # the error Playwright itself raises when the browser binary isn't
        # installed on this machine. Only this narrow case skips -- anything
        # else below is a real defect and must fail loudly.
        pytest.skip(
            f"{module_path}: Chromium not installed on this machine "
            f"({type(e).__name__}: {e})"
        )
    except ImportError as e:
        missing = getattr(e, "name", "") or ""
        if missing == "playwright" or missing.startswith("playwright."):
            # Same skip category as above: the playwright package itself
            # isn't installed on this machine, so the module never got as
            # far as launching a browser.
            pytest.skip(
                f"{module_path}: playwright package not installed "
                f"({type(e).__name__}: {e})"
            )
        pytest.fail(
            f"{module_path}: failed to import ({type(e).__name__}: {e}) -- "
            f"this is a real defect (not a missing browser) that would "
            f"break every production dispatch to this backend"
        )
    except Exception as e:
        pytest.fail(
            f"{module_path}: failed to import ({type(e).__name__}: {e}) -- "
            f"this is a real defect (not a missing browser) that would "
            f"break every production dispatch to this backend"
        )

    fn = getattr(module, "action_recharge_account", None)
    if fn is None:
        pytest.fail(f"{module_path}: has no action_recharge_account")

    try:
        # bind_partial (not bind): tolerates the missing `page` param below,
        # but for the same reason it also silently tolerates any OTHER
        # missing param a backend might start requiring -- this test cannot
        # catch that; only extra/renamed keys the signature rejects.
        inspect.signature(fn).bind_partial(**DISPATCH_PAYLOAD)
    except TypeError as e:
        pytest.fail(
            f"{module_path}.action_recharge_account cannot bind the real "
            f"dispatch payload -- this TypeError would be raised at the "
            f"dispatcher's call boundary, before the function's own try "
            f"block, stranding the wallet deduction with no restore: {e}"
        )
