from celery_app import celery_app
from .dispatcher import invoke_backend_action
from common.utils.cleanup import cleanup_backend_dirs
from pathlib import Path
from settings import BASE_DIR

@celery_app.task(name="automation.invoke_action", bind=True)
def invoke_action(self, backend: str, action: str, **kwargs):
    try:
        task_id = self.request.id
        kwargs["task_id"] = task_id
        return invoke_backend_action(backend, action, **kwargs)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries, max_retries=3)


@celery_app.task(name="cleanup.cleanup_backend_dirs", queue="default")
def cleanup_task():
    backends_root = Path(BASE_DIR) / "backends"
    cleanup_backend_dirs(backends_root)