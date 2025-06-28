import os
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("APP_KEY")
APP_ENV = os.getenv("APP_ENV", "production")
HEADLESS = os.getenv("HEADLESS", "True").lower() == "true"
DEBUG = os.getenv("DEBUG", "True").lower() == "true"