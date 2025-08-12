import os
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_ENV = os.getenv("APP_ENV", "production")
HEADLESS = os.getenv("HEADLESS", "True").lower() == "true"
DEBUG = os.getenv("DEBUG", "True").lower() == "true"


DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS").strip('"')
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME")


ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY")


CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME", "casino-automation-screenshots")

API_DELAY_SECONDS = 1

import os
# Base path for your project root (so we can find backends/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))