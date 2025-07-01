import os
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_ENV = os.getenv("APP_ENV", "production")
HEADLESS = os.getenv("HEADLESS", "True").lower() == "true"
DEBUG = os.getenv("DEBUG", "True").lower() == "true"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)