from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.milkyway.config import *
from backends.milkyway.utils.credentials import generate_credentials

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
import logging

def _login_and_navigate(logger: logging.Logger):
    """Launch browser, log in, and return browser objects."""
    logger.info("Launching browser (headed)...")
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        logger.debug("Browser launched successfully.")
    except Exception as launch_exc:
        logger.exception("Failed to launch browser or context: %s", launch_exc)
        playwright.stop()
        raise

    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")


        for attempt in range(MAX_CAPTCHA_RETRIES):
            page.wait_for_selector(LOGIN_ACCOUNT, timeout=15_000)
            page.fill(LOGIN_ACCOUNT, USERNAME)
            page.fill(LOGIN_PASSWORD, PASSWORD)

            logger.debug("Solving CAPTCHA.")
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)

            if text == 0:
                page.reload(wait_until="domcontentloaded")
                continue

            page.fill(CAPTCHA_INPUT, text)

            if DEBUG:
                input("DEBUG MODE: Enter CAPTCHA manually, then press Enter to continue...")

            page.click(LOGIN_BUTTON)

            try:
                captcha_status = page.wait_for_selector("div#mb_con", timeout=3000)
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

        left_iframe_el = page.query_selector(LEFT_IFRAME)
        if not left_iframe_el or not left_iframe_el.content_frame():
            raise Exception("Left iframe missing or inaccessible.")

        left_frame = left_iframe_el.content_frame()
        left_frame.wait_for_selector(USER_MANAGEMENT_XPATH, timeout=10_000)
        left_frame.click(USER_MANAGEMENT_XPATH)

        logger.info("Login and navigation successful.")
        return playwright, browser, context, page

    except PlaywrightTimeoutError as to_err:
        logger.exception("Timeout during login/navigation: %s", to_err)
        return None
    except Exception as e:
        logger.exception("Login/navigation error: %s", e)
        return None


def _create_single_account(page, logger: logging.Logger):
    """Create a single account and save credentials."""
    try:
        page.wait_for_selector(MAIN_IFRAME, timeout=10_000)
        main_iframe_el = page.query_selector(MAIN_IFRAME)
        if not main_iframe_el or not main_iframe_el.content_frame():
            raise Exception("Main iframe missing or inaccessible.")

        main_frame = main_iframe_el.content_frame()
        main_frame.wait_for_selector(CREATE_ACCOUNT_INIT, timeout=10_000)
        main_frame.click(CREATE_ACCOUNT_INIT)

        iframe_dialog_el = page.wait_for_selector(CREATE_ACCOUNT_DIALOG, timeout=15_000)
        dialog_frame = iframe_dialog_el.content_frame()
        if not dialog_frame:
            raise Exception("Cannot access dialog iframe.")

        while True:
            account_id, password = generate_credentials()
            dialog_frame.fill(ACCOUNT_ID, account_id)
            dialog_frame.fill(ACCOUNT_PASSWORD, password)
            dialog_frame.fill(CONFIRM_PASSWORD, password)

            dialog_frame.locator(CREATE_ACCOUNT).wait_for(state="visible", timeout=10_000)
            dialog_frame.click(CREATE_ACCOUNT)

            try:
                page.wait_for_selector("#mb_con", timeout=5_000, state="attached")
                message_elem = page.query_selector("#mb_msg")
                message_text = message_elem.inner_text().lower() if message_elem else ""

                if "the account number already exists" in message_text:
                    logger.info("🔁 Account ID already exists. Retrying with new credentials...")
                    ok_button = page.wait_for_selector("#mb_btn_ok", timeout=5000, state="attached")
                    if ok_button:
                        ok_button.click()
                    continue  # Retry the loop with new credentials

                if any(phrase in message_text for phrase in ACCOUNT_SUCCESS_MSG):
                    logger.info("✅ Account created successfully.")
                    save_credentials(account_id, password, logger, DATA_DIR)
                    ok_button = page.wait_for_selector("#mb_btn_ok", timeout=5000, state="attached")
                    if ok_button:
                        ok_button.click()
                    break  # Exit loop on success
                else:
                    logger.warning("⚠️ Unexpected message: %s", message_text)
                    break  # Exit if it's some other unknown message

            except PlaywrightTimeoutError:
                logger.warning("⚠️ Success dialog not detected.")
                break  # Exit if no success dialog appears


    except PlaywrightTimeoutError as to_err:
        logger.exception("Timeout during account creation: %s", to_err)
    except Exception as e:
        logger.exception("Account creation error: %s", e)


def action_create_account(count: int):
    """Main entry to create `count` number of accounts."""
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)

    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting create-account action: count=%d, backend=%s =====", count, BACKEND_NAME)

    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = _login_and_navigate(logger)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger)
            page.reload(wait_until="domcontentloaded")
    except Exception as e:
        logger.exception("Error during account creation: %s", e)
    finally:
        logger.info("===== Create-account action completed. Closing browser. =====")
        try:
            if browser:
                browser.close()
                logger.debug("Browser closed.")
            if playwright:
                playwright.stop()
                logger.debug("Playwright stopped.")
        except Exception as close_exc:
            logger.exception("Error while closing resources: %s", close_exc)
