
import os
import sys
from pathlib import Path

project_root = os.getcwd()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from celery import Celery
from celery.schedules import crontab
from kombu import Queue, Exchange

# settings.py should export CELERY_BROKER_URL and CELERY_RESULT_BACKEND
from settings import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

# -------------------------------------------------------------------
# 1) Create the Celery app
# -------------------------------------------------------------------
celery_app = Celery(
    "casino_automation",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["api.tasks"],   # your task definitions, including cleanup
)

# -------------------------------------------------------------------
# 2) Timezone configuration
# -------------------------------------------------------------------
# Disable UTC, use Asia/Karachi
celery_app.conf.enable_utc = False
celery_app.conf.timezone = "Asia/Karachi"

# -------------------------------------------------------------------
# 3) Declare all queues
# -------------------------------------------------------------------
celery_app.conf.task_queues = (
    Queue("default",    Exchange("default"),    routing_key="default"),
    Queue("juwa",       Exchange("juwa"),       routing_key="juwa"),
    Queue("milkyway",   Exchange("milkyway"),   routing_key="milkyway"),
    Queue("gameroom",   Exchange("gameroom"),   routing_key="gameroom"),
    Queue("orionstars", Exchange("orionstars"), routing_key="orionstars"),
    Queue("gamevault",  Exchange("gamevault"),  routing_key="gamevault"),
    Queue("ultrapanda", Exchange("ultrapanda"), routing_key="ultrapanda"),
    Queue("pandamaster",Exchange("pandamaster"),routing_key="pandamaster"),
    Queue("vblink",     Exchange("vblink"),     routing_key="vblink"),
    Queue("river",      Exchange("river"),      routing_key="river"),
    Queue("firekirin",  Exchange("firekirin"),  routing_key="firekirin"),
)

# -------------------------------------------------------------------
# 4) Route tasks to queues
# -------------------------------------------------------------------
celery_app.conf.task_routes = {
    # core orchestration
    "automation.invoke_action":               {"queue": "default"},
    # cleanup task you defined in api.tasks
    "cleanup.cleanup_backend_dirs":           {"queue": "default"},

    # per-backend automation actions
    "backends.juwa.automation.action_*":       {"queue": "juwa"},
    "backends.milkyway.automation.action_*":   {"queue": "milkyway"},
    "backends.gameroom.automation.action_*":   {"queue": "gameroom"},
    "backends.orionstars.automation.action_*": {"queue": "orionstars"},
    "backends.gamevault.automation.action_*":  {"queue": "gamevault"},
    "backends.ultrapanda.automation.action_*": {"queue": "ultrapanda"},
    "backends.pandamaster.automation.action_*":{"queue": "pandamaster"},
    "backends.vblink.automation.action_*":     {"queue": "vblink"},
    "backends.river.automation.action_*":      {"queue": "river"},
    "backends.firekirin.automation.action_*":  {"queue": "firekirin"},
}

# -------------------------------------------------------------------
# 5) Worker & task defaults
# -------------------------------------------------------------------
celery_app.conf.update(
    # acknowledge tasks after execution
    task_acks_late=True,
    # ensure fair scheduling
    worker_prefetch_multiplier=1,
    # concurrency from env or default to 8
    worker_concurrency=int(os.getenv("WORKER_CONCURRENCY", 8)),
    # hard and soft time limits (seconds) → 10 minutes
    task_time_limit=1800,
    task_soft_time_limit=1800,
    # JSON serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # connection pooling
    broker_pool_limit=None,
    broker_transport_options={"max_connections": 1000},
)

# -------------------------------------------------------------------
# 6) Periodic (Beat) schedule
# -------------------------------------------------------------------
# Run once every 24h at 00:00 Asia/Karachi, on the 'default' queue
celery_app.conf.beat_schedule = {
    "daily-backend-cleanup": {
        "task": "cleanup.cleanup_backend_dirs",
        "schedule": crontab(hour=0, minute=0),
        "options": {"queue": "default"},
    },
}

# -------------------------------------------------------------------
# 7) Auto-discover any other tasks in api.tasks
# -------------------------------------------------------------------
celery_app.autodiscover_tasks(["api.tasks"])
