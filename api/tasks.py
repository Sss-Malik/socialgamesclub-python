from celery_app import celery_app
from common.utils.db_actions import update_automation_result
from .dispatcher import invoke_backend_action
from common.utils.cleanup import cleanup_backend_dirs
from pathlib import Path
from settings import BASE_DIR
from datetime import datetime

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