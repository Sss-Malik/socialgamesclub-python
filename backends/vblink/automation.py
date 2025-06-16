import logging

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.vblink.config import *
from backends.vblink.utils.actions import click_set_score
from common.utils.logger import get_backend_logger
from common.utils.credential_utils import generate_credentials
from common.utils.ensure_directories import ensure_directories
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

        page.click(LOGIN_BUTTON)
        page.wait_for_selector(MAIN_PAGE_EL, timeout=20_000)
        logger.info("Login successful.")

        if URL_CHANGE:
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
        page.click(CREATE_ACCOUNT)

        try:
            message = page.wait_for_selector(ACCOUNT_SUCCESS, timeout=3000, state="attached")
            if any(phrase in message.inner_text().lower() for phrase in ACCOUNT_SUCCESS_MSG):
                logger.info("✅ Account created successfully.")
                save_credentials(account_id, password, logger, DATA_DIR)
            else:
                logger.warning("⚠️ Unexpected success message: %s", message.inner_text())
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


def _recharge_account(page, logger: logging.Logger, count: int, account_id):
    try:

        attempts = 5

        for attempt in range(attempts):
            page.goto(SEARCH_URL, wait_until="domcontentloaded")

            player_radio_label = page.locator("label.el-radio", has_text="Player account")

            # Click the entire label (safe for custom-styled radios)
            player_radio_label.click()

            page.wait_for_selector(ACCOUNT_SEARCH_INPUT, timeout=15_000)
            page.fill(ACCOUNT_SEARCH_INPUT, account_id)
            page.click('button:has-text("Ok")')

            page.wait_for_timeout(2000)

            click_set_score(page, account_id, logger)

            try:
                error_locator = page.locator("p.el-message__content")
                error_locator.wait_for(state="visible", timeout=5000)

                error_text = error_locator.inner_text().strip().lower()

                if "error: 167" in error_text and "frequency of requests is too high" in error_text:
                    logger.warning("⚠️ Request frequency too high. Backing off...")
                    page.reload(wait_until="domcontentloaded")
                    continue
                else:
                    logger.info(f"ℹ️ Message received: {error_text}")

            except PlaywrightTimeoutError:
                logger.info("✅ No error message appeared. Continuing...")


            input_score_el = page.wait_for_selector('input[placeholder="Set points : ie 100"]', timeout=5000)
            input_score_el.fill(str(count))

            input("enter")

            ok_button = page.locator(
                "//div[contains(@class, 'el-form-item__content') and .//span[text()='Cancel']]//span[text()='OK']")

            ok_button.click()

            try:
                success_alert = page.locator("div.el-message.el-message--success")
                success_alert.wait_for(state="visible", timeout=5000)
                alert_text = success_alert.inner_text()

                if "sucessful operation" in alert_text.lower():
                    logger.info("✅ Success confirmed.")
                    break
                else:
                    logger.error(f"❌ Purchase failed: {alert_text}")

            except PlaywrightTimeoutError:
                logger.warning("⚠️ No purchase confirmation dialog appeared after confirming recharge.")
            except Exception as e:
                logger.exception(f"❌ Error verifying purchase success: {e}")

    except PlaywrightTimeoutError as to_err:
        logger.exception("⏳ Timeout during account topup: %s", to_err)
    except Exception as e:
        logger.exception("❌ Account topup error: %s", e)



def action_recharge_account(count: int, account_id):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)

    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting topup action: count=%d, account_id=%s", count, account_id)

    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = _login_and_navigate(logger)
        _recharge_account(page, logger, count, account_id)
    except Exception as e:
        logger.exception("Error during account creation: %s", e)
    finally:
        logger.info("===== topup-account action completed. Closing browser. =====")
        try:
            if browser:
                browser.close()
                logger.debug("Browser closed.")
            if playwright:
                playwright.stop()
                logger.debug("Playwright stopped.")
        except Exception as close_exc:
            logger.exception("Error while closing resources: %s", close_exc)
