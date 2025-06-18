import logging

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.river.config import *
from backends.river.utils.credentials import generate_credentials
from backends.river.utils.actions import click_purchase_for_account

from common.utils.logger import get_backend_logger
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
        page.wait_for_selector(MAIN_PAGE_EL, timeout=20_000, state="attached")
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
        page.wait_for_selector(CREATE_ACCOUNT_INIT, timeout=15_000)

        account_id, password = generate_credentials()
        logger.info("Generated credentials: %s / [REDACTED]", account_id)

        page.fill(ACCOUNT_ID, account_id)
        page.fill(ACCOUNT_BALANCE, "0")
        page.click(CREATE_ACCOUNT)

        try:
            # Wait for the success alert to be attached and visible
            message = page.wait_for_selector(ACCOUNT_SUCCESS, timeout=3000, state="visible")

            message_text = " ".join(message.inner_text().lower().split())
            if any(phrase in message_text for phrase in ACCOUNT_SUCCESS_MSG):
                logger.info("✅ Account created successfully")
                save_credentials(account_id, password, logger, DATA_DIR)
            else:
                logger.warning("⚠️ Unexpected success message: %s", message_text)

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
                page.reload(wait_until="domcontentloaded")
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

        page.wait_for_selector(ACCOUNT_SEARCH_INPUT, timeout=15_000)
        page.fill(ACCOUNT_SEARCH_INPUT, account_id)
        page.click('button:has-text("Search")')

        click_purchase_for_account(page, account_id, logger)
        page.wait_for_timeout(2000)

        deposit_modal = page.wait_for_selector("#modal-deposite", timeout=15000, state="visible")

        # Fill the amount input
        amount_input = deposit_modal.query_selector("input#modal-deposite-amount")
        if amount_input:
            amount_input.fill(str(count))
            logger.info(f"✅ Filled deposit amount with: {count}")
        else:
            logger.error("❌ Amount input not found in deposit modal.")
            return

        input("🔍 Press Enter to continue (e.g., after verifying or adjusting inputs)...")

        purchase_button = deposit_modal.query_selector("input.btn.btn-primary[type='submit'][value='Purchase']")
        if purchase_button:
            purchase_button.click()
            logger.info("✅ Clicked Purchase button in modal.")
        else:
            logger.error("❌ Purchase button not found in modal.")

        try:
            # Try to wait for either alert (error or success)
            error_alert = None
            success_alert = None

            try:
                error_alert = page.wait_for_selector(".alert.alert-error", timeout=3000, state="visible")
            except PlaywrightTimeoutError:
                pass  # No error alert appeared

            try:
                success_alert = page.wait_for_selector(".alert.alert-success", timeout=3000, state="visible")
            except PlaywrightTimeoutError:
                pass  # No success alert appeared

            if error_alert:
                error_text = error_alert.inner_text().strip().lower()
                logger.error(f"❌ Purchase failed: {error_text}")

                if "not enough credits in your balance" in error_text:
                    logger.info(f"Account purchase failed due to insufficient credits.")
            elif success_alert:
                success_text = success_alert.inner_text().strip().lower()
                if "amount added" in success_text:
                    logger.info(f"✅ Purchase completed successfully: {success_text}")
            else:
                logger.warning("⚠️ No success or error message appeared after confirming recharge.")

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
