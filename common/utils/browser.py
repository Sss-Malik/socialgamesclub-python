from functools import wraps
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
from settings import HEADLESS, DEBUG


def with_browser(func):
    """
    Decorator to manage Playwright browser lifecycle: launch before calling the wrapped
    function and close when done. The wrapped function must accept a `page: Page` as its first argument.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Initialize Playwright and browser
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )
            # Create browser context
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/115.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )
            # Evade automation detection
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            # Open a new page
            page: Page = context.new_page()
            try:
                # Call the wrapped function, passing the page
                return func(page, *args, **kwargs)
            finally:
                # Always close browser
                browser.close()
    return wrapper
