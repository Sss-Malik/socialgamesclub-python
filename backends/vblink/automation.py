from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.vblink.config import LOGS_DIR, LOGIN_URL, USERNAME, PASSWORD, DATA_DIR, BACKEND_NAME, USER_MANAGEMENT_URL

from common.logger import get_backend_logger
from common.captcha_solver import solve_captcha_with_retries
from common.credential_utils import generate_credentials


def run():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Set up logger (writes to both console + logs/automation.log)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info(f"Starting automation for backend: {BACKEND_NAME}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            logger.info(f"Opening login page: {LOGIN_URL}")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            page.wait_for_selector('input[name="userName"]', timeout=15_000)
            page.fill('input[name="userName"]', USERNAME)
            page.fill('input[name="passWd"]', PASSWORD)
            page.wait_for_timeout(1500)
            page.click('button:has-text("Login")')

            page.wait_for_selector('section.app-main', timeout=15_000)

            page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

            page.click('button:has-text("Add Player")')

            page.wait_for_selector('input[placeholder="Player’s account name (7-16 characters)"]')

            new_username, new_password = generate_credentials()

            page.fill('input[placeholder="Player’s account name (7-16 characters)"]', new_username)
            page.fill(
                'input[placeholder="Length must be 6-16 characters! Must include a combination of numbers and letters, and allows some special characters: !@#$%^/.,()"]',
                new_password)

            page.click('button:has-text("OK")')

            created_file = DATA_DIR / "created_players.txt"
            with created_file.open("a", encoding="utf-8") as f:
                f.write(f"{new_username}:{new_password}\n")
            logger.info(f"✅ Created player saved: {new_username}:{new_password}")

            input("Press Enter to close the browser...")


        except PlaywrightTimeoutError as te:
            logger.error(f"TimeoutError: {te}")
        except Exception as e:
            logger.exception(f"Unhandled Exception: {e}")
        finally:
            logger.info("Automation run completed. Browser remains open (headed mode).")
            # If you want browser to close automatically, uncomment:
            # browser.close()