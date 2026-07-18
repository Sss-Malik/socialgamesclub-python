from uuid import uuid4

from celery_app import celery_app
from common.utils.db_actions import (
    update_automation_result,
    get_backends_below_unassigned_threshold,
    insert_automation_result_and_request,
)
from .dispatcher import invoke_backend_action
from common.utils.cleanup import cleanup_backend_dirs
from pathlib import Path
from settings import BASE_DIR
from datetime import datetime

# Minimum number of unassigned accounts a backend should keep on hand.
UNASSIGNED_ACCOUNT_THRESHOLD = 10

# Backends the auto-replenish task must never create accounts for.
#
# yolo, cashfrenzy and cashmachine were integrated 2026-07-18 and their
# create-account paths have not yet been exercised against the live vendors.
# Replenish runs unattended every 10 minutes and would create
# `accounts_creation_pd` accounts per pass, so an unverified backend could
# burn through vendor-side account limits — or silently fail every 10
# minutes — before anyone looked. Pool top-ups stay manual (via
# /automation/create-account) until each backend has a verified run.
#
# Drop a name from this set once its create-account has been confirmed
# working in production.
REPLENISH_EXEMPT_BACKENDS = frozenset({"yolo", "cashfrenzy", "cashmachine"})

@celery_app.task(name="automation.invoke_action", bind=True)
def invoke_action(self, backend: str, action: str, **kwargs):
    start_ts = datetime.utcnow()
    task_id = self.request.id
    try:
        kwargs["task_id"] = task_id
        return invoke_backend_action(backend, action, **kwargs)
    finally:
        end_ts = datetime.utcnow()
        duration = (end_ts - start_ts).total_seconds()
        update_automation_result(task_id=task_id, duration_seconds=duration)


@celery_app.task(name="cleanup.cleanup_backend_dirs", queue="default")
def cleanup_task():
    backends_root = Path(BASE_DIR) / "backends"
    cleanup_backend_dirs(backends_root)


@celery_app.task(name="replenish.replenish_backend_accounts", queue="default")
def replenish_backend_accounts():
    """
    Beat task: for every backend whose unassigned-account pool has dropped below
    UNASSIGNED_ACCOUNT_THRESHOLD, kick off a create-account run.

    Mirrors the side effects of the /automation/create-account endpoint without an
    HTTP hop: it writes the AutomationResult + AutomationRequest rows and enqueues
    `invoke_action` on the backend's own queue. Each create-account run produces
    `backend_games.accounts_creation_pd` accounts.

    Backends in REPLENISH_EXEMPT_BACKENDS are skipped entirely — no task and no
    AutomationResult row, since a row with no task behind it would sit in
    `pending` forever and read as a hung run.
    """
    backends = get_backends_below_unassigned_threshold(UNASSIGNED_ACCOUNT_THRESHOLD)

    triggered = []
    skipped = []
    for backend_id, backend_name in backends:
        if backend_name in REPLENISH_EXEMPT_BACKENDS:
            skipped.append(backend_name)
            continue

        task_id = str(uuid4())
        insert_automation_result_and_request(
            user_id=None,
            description="Auto-replenish: initiate account creation",
            task_id=task_id,
            backend_id=backend_id,
            order_id=None,
            payload={
                "action": "create-account",
                "backend": backend_name,
                "source": "auto-replenish",
            },
            request_type="create",
        )
        invoke_action.apply_async(
            args=[backend_name, "create-account"],
            kwargs={},
            queue=backend_name,
            task_id=task_id,
        )
        triggered.append(backend_name)

    # `skipped` is reported so an exempt backend sitting below threshold is
    # visible in the Celery result rather than looking like the task never
    # noticed it.
    return {"triggered": triggered, "count": len(triggered), "skipped": skipped}