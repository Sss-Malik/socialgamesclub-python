import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.juwa.config import *
from common.utils.logger import get_backend_logger
from common.utils.credential_utils import generate_credentials
from common.utils.ensure_directories import ensure_directories
from common.utils.handle_captcha import handle_captcha
from common.utils.save_credentials import save_credentials

def _login_and_navigate(logger: logging.Logger):
    logger.info("Launching browser via Playwright (headed mode)...")
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        logger.info("Navigating to login page: %s", LOGIN_URL)
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        for attempt in range(MAX_CAPTCHA_RETRIES):
            page.wait_for_selector(LOGIN_ACCOUNT, timeout=15_000)
            page.fill(LOGIN_ACCOUNT, USERNAME)
            page.fill(LOGIN_PASSWORD, PASSWORD)

            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)

            if text == 0:
                page.reload(wait_until="domcontentloaded")
                continue

            page.fill(CAPTCHA_INPUT, text)

            if DEBUG:
                input("DEBUG: Check complete CAPTCHA and press Enter...")

            page.click(LOGIN_BUTTON)

            try:
                captcha_status = page.wait_for_selector("div.el-message-box", timeout=3000)
                text = captcha_status.inner_text()
                if "incorrect" in text.lower():
                    logger.warning("Captcha failed. Retrying...")
                    solver.report_incorrect_image_captcha()
                    page.reload(wait_until="domcontentloaded")
                    continue
            except PlaywrightTimeoutError:
                logger.info("No captcha incorrect message found. Assuming correct.")
                break

        page.wait_for_selector(MAIN_PAGE_EL, timeout=20_000)
        logger.info("Login successful.")


        page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

        return playwright, browser, context, page

    except Exception as e:
        logger.exception("Login error: %s", e)
        try: browser.close()
        except: pass
        playwright.stop()
        raise

def _create_single_account(page, logger: logging.Logger):
    try:
        page.wait_for_selector(CREATE_ACCOUNT_INIT, timeout=15_000).click()
        page.wait_for_selector(ACCOUNT_ID, timeout=10_000)

        account_id, password = generate_credentials(BACKEND_SIGNATURE)
        logger.info("Generated credentials: %s / [REDACTED]", account_id)

        page.fill(ACCOUNT_ID, account_id)
        page.fill(ACCOUNT_PASSWORD, password)
        page.fill(CONFIRM_PASSWORD, password)
        page.click(CREATE_ACCOUNT)

        try:
            el = page.wait_for_selector(ACCOUNT_SUCCESS, timeout=3_000, state="attached")
            text = el.inner_text().lower()
            if any(phrase in text for phrase in ACCOUNT_SUCCESS_MSG):
                logger.info("✅ Account created successfully.")
                save_credentials(account_id, password, logger, DATA_DIR)
            else:
                logger.warning("⚠️ Unexpected success message: %s", text)
        except PlaywrightTimeoutError:
            logger.warning("⚠️ No success message after creating account.")
    except Exception as e:
        logger.exception("Account creation failed: %s", e)


def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("==== Starting account creation (%d accounts) ====", count)

    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = _login_and_navigate(logger)

        for i in range(count):
            logger.info("Creating account #%d of %d", i + 1, count)
            _create_single_account(page, logger)

            try:
                page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning("Failed to reload User Management page: %s", e)

    except Exception as e:
        logger.exception("Fatal error in account creation loop: %s", e)
    finally:
        logger.info("==== Finished account creation ====")
        # Uncomment to auto-close browser
        try:
            browser.close()
            playwright.stop()
            logger.debug("Browser and Playwright closed.")
        except Exception as e:
            logger.warning("Error closing browser/playwright: %s", e)


def action_account_topup(count: int):
    """
    Stub for future implementation.
    """
    pass
