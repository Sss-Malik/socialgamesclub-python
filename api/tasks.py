from celery_app import celery_app
from .main import _invoke_action

@celery_app.task(name="automation.invoke_action", bind=True)
def invoke_action(self, backend: str, action: str, **kwargs):
    """
    Dispatches to your existing _invoke_action function.
    Retries on exception by default.
    """
    try:
        return _invoke_action(backend, action, **kwargs)
    except Exception as exc:
        # retry up to 3 times with exponential back-off
        raise self.retry(exc=exc, countdown=2 ** self.request.retries, max_retries=3)
