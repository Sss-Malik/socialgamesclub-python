from celery_app import celery_app
from .dispatcher import invoke_backend_action

@celery_app.task(name="automation.invoke_action", bind=True)
def invoke_action(self, backend: str, action: str, **kwargs):
    try:
        return invoke_backend_action(backend, action, **kwargs)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries, max_retries=3)