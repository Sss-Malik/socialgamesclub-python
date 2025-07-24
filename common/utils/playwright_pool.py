from playwright.sync_api import sync_playwright
from threading import Semaphore
import os

from settings import HEADLESS

MAX_CONTEXTS = int(os.getenv("MAX_CONTEXTS", 10))
_CONTEXT_SEM = Semaphore(MAX_CONTEXTS)

MAX_PAGES = int(os.getenv("MAX_PAGES", 100))
PAGE_SEM = Semaphore(MAX_PAGES)

# Start Playwright once per worker
_play = sync_playwright().start()
BROWSER = _play.chromium.launch(
    headless=HEADLESS,
    args=[
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
)