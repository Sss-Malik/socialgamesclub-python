import os, sys
project_root = os.getcwd()            # /app
if project_root not in sys.path:
    sys.path.insert(0, project_root)

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
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=int(os.getenv("WORKER_CONCURRENCY", 8)),
    task_time_limit=300,
    task_soft_time_limit=240,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "automation.invoke_action": {"queue": "default"},
        "backends.juwa.automation.action_*": {"queue": "juwa"},
        "backends.milkyway.automation.action_*": {"queue": "milkyway"},
        "backends.gameroom.automation.action_*": {"queue": "gameroom"},
        "backends.orionstars.automation.action_*": {"queue": "orionstars"},
        "backends.gamevault.automation.action_*": {"queue": "gamevault"},
        "backends.ultrapanda.automation.action_*": {"queue": "ultrapanda"},
        "backends.pandamaster.automation.action_*": {"queue": "pandamaster"},
        "backends.vblink.automation.action_*": {"queue": "vblink"},
        "backends.river.automation.action_*": {"queue": "river"},
        "backends.firekirin.automation.action_*": {"queue": "firekirin"},
    },
)

celery_app.autodiscover_tasks(["api.tasks"])