import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.gameroom.config import *
from backends.gameroom.utils.credentials import generate_credentials

from common.utils.logger import get_backend_logger
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

        page.wait_for_selector(LOGIN_ACCOUNT, timeout=15_000)
        page.fill(LOGIN_ACCOUNT, USERNAME)
        page.fill(LOGIN_PASSWORD, PASSWORD)

        if CAPTCHA:
            handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_INPUT, CAPTCHA_DIR)

        if DEBUG:
            try:
                input("DEBUG: Manually complete CAPTCHA and press Enter...")
            except Exception:
                logger.warning("DEBUG input skipped.")

        page.click(LOGIN_BUTTON)
        page.wait_for_selector(MAIN_PAGE_EL, timeout=20_000, state="attached")
        logger.info("Login successful.")

        page.click('a:has-text("Game User")')

        page.wait_for_selector(USER_MANAGEMENT_EL, timeout=5000, state="visible").click()

        return playwright, browser, context, page

    except Exception as e:
        logger.exception("Login error: %s", e)
        try: browser.close()
        except: pass
        playwright.stop()
        raise

def _create_single_account(page, logger: logging.Logger):
    try:
        main_iframe_el = page.wait_for_selector(MAIN_IFRAME, timeout=15_000)
        if not main_iframe_el or not main_iframe_el.content_frame():
            raise Exception("Main iframe missing or inaccessible.")
        main_frame = main_iframe_el.content_frame()

        main_frame.wait_for_selector(CREATE_ACCOUNT_INIT, timeout=15_000, state="visible").click()

        dialog_iframe_el = main_frame.wait_for_selector(DIALOG_IFRAME, timeout=15_000)
        if not dialog_iframe_el or not dialog_iframe_el.content_frame():
            raise Exception("Dialog iframe missing or inaccessible.")
        dialog_iframe = dialog_iframe_el.content_frame()

        dialog_iframe.wait_for_selector(ACCOUNT_ID, timeout=10_000)

        account_id, password = generate_credentials()
        logger.info("Generated credentials: %s / [REDACTED]", account_id)

        dialog_iframe.fill(ACCOUNT_ID, account_id)
        dialog_iframe.fill(ACCOUNT_BALANCE, "0")
        dialog_iframe.fill(ACCOUNT_PASSWORD, password)
        dialog_iframe.fill(CONFIRM_PASSWORD, password)
        dialog_iframe.click(CREATE_ACCOUNT)

        try:
            el = main_frame.wait_for_selector(ACCOUNT_SUCCESS, timeout=5000)
            text = el.inner_text().lower()
            if any(phrase in text for phrase in ACCOUNT_SUCCESS_MSG):
                logger.info("✅ Account created successfully.")
                save_credentials(account_id, password, logger, DATA_DIR)
                main_frame.click(ACCOUNT_SUCCESS_CLOSE)
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
            page.wait_for_timeout(2000)
            _create_single_account(page, logger)


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
