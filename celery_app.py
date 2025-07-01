import os
from celery import Celery
from settings import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

celery_app = Celery(
    "casino_automation",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["api.tasks"]     # where we’ll define our tasks
)

# optional: configure serialization, timezones, task routes...
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
