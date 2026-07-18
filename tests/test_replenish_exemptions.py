"""The auto-replenish beat task must skip exempted backends.

`replenish_backend_accounts` runs every 10 minutes and enqueues a
create-account run for any backend whose unassigned pool has dropped below
UNASSIGNED_ACCOUNT_THRESHOLD. Each run creates `accounts_creation_pd`
accounts against a live vendor, unattended.

Backends listed in REPLENISH_EXEMPT_BACKENDS must never be enqueued by it,
and — just as importantly — must not have an AutomationResult row written
for them either, since a row with no task behind it shows up in the UI as a
run that hangs in `pending` forever.
"""
import api.tasks as tasks


class _Recorder:
    """Stands in for invoke_action.apply_async and records what was enqueued."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, args, kwargs, queue, task_id):
        self.calls.append({"args": args, "queue": queue, "task_id": task_id})


def _patch(monkeypatch, below_threshold):
    """Point the task at a fixed backend list and capture its side effects."""
    monkeypatch.setattr(
        tasks, "get_backends_below_unassigned_threshold", lambda *_a, **_k: below_threshold
    )

    written = []
    monkeypatch.setattr(
        tasks,
        "insert_automation_result_and_request",
        lambda **kw: written.append(kw),
    )

    recorder = _Recorder()
    monkeypatch.setattr(tasks.invoke_action, "apply_async", recorder)
    return recorder, written


def test_exempt_backends_are_not_enqueued(monkeypatch):
    """All three new backends are below threshold; none may be enqueued."""
    below = [
        (4, "juwa"),
        (13, "yolo"),
        (14, "cashfrenzy"),
        (15, "cashmachine"),
        (11, "goldentreasure"),
    ]
    recorder, written = _patch(monkeypatch, below)

    result = tasks.replenish_backend_accounts()

    assert result["triggered"] == ["juwa", "goldentreasure"]
    assert result["count"] == 2
    # Reported positively, not just absent from `triggered`: an exempt backend
    # below threshold should be visible in the result, not invisible.
    assert result["skipped"] == ["yolo", "cashfrenzy", "cashmachine"]

    enqueued = [c["queue"] for c in recorder.calls]
    assert enqueued == ["juwa", "goldentreasure"]
    for exempt in ("yolo", "cashfrenzy", "cashmachine"):
        assert exempt not in enqueued

    # No orphan bookkeeping rows: an AutomationResult with no task behind it
    # would sit in `pending` forever.
    assert [w["payload"]["backend"] for w in written] == ["juwa", "goldentreasure"]


def test_non_exempt_backends_still_replenish(monkeypatch):
    """The exemption must not disable the task for everyone else."""
    recorder, written = _patch(monkeypatch, [(4, "juwa"), (2, "gameroom")])

    result = tasks.replenish_backend_accounts()

    assert result["triggered"] == ["juwa", "gameroom"]
    assert [c["queue"] for c in recorder.calls] == ["juwa", "gameroom"]
    assert len(written) == 2


def test_all_exempt_is_a_no_op(monkeypatch):
    """Every candidate exempt: task returns cleanly, enqueues nothing."""
    below = [(13, "yolo"), (14, "cashfrenzy"), (15, "cashmachine")]
    recorder, written = _patch(monkeypatch, below)

    result = tasks.replenish_backend_accounts()

    assert result == {
        "triggered": [],
        "count": 0,
        "skipped": ["yolo", "cashfrenzy", "cashmachine"],
    }
    assert recorder.calls == []
    assert written == []


def test_exempt_set_contents():
    """Pin the exempt list so adding or dropping a backend is a deliberate act."""
    assert tasks.REPLENISH_EXEMPT_BACKENDS == frozenset(
        {"yolo", "cashfrenzy", "cashmachine"}
    )
