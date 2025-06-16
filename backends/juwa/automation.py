import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from backends.juwa.config import *
from backends.juwa.utils.actions import click_recharge_for_account

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



def _recharge_account(page, logger: logging.Logger, count: int, account_id):
    try:

        page.wait_for_selector(ACCOUNT_SEARCH_INPUT, timeout=15_000)
        page.fill(ACCOUNT_SEARCH_INPUT, account_id)
        page.click('button:has-text("search")')

        click_recharge_for_account(page, account_id, logger)
        page.wait_for_timeout(2000)

        recharge_input = page.wait_for_selector("//label[text()='Recharge Amount']/following-sibling::div//input", timeout=15_000)
        recharge_input.fill(str(count))

        input("Press Enter to continue...")

        # Step 1: Locate the dialog by its title
        dialog = page.locator("div.el-dialog", has=page.locator("span.el-dialog__title",
                                                                has_text="Please confirm your recharge & details!"))

        # Step 2: Within that dialog, find the Confirm button
        confirm_button = dialog.locator(".el-dialog__footer >> button.el-button--primary:has-text('Confirm')")

        # Step 3: Wait for the Confirm button to be visible and click it
        confirm_button.wait_for(state="visible", timeout=10000)
        confirm_button.click()

        try:
            # Wait for invoice dialog visible
            invoice_model = page.wait_for_selector("#invoiceModel", timeout=10000, state="visible")

            # Find all <p> elements inside invoiceModel
            paragraphs = invoice_model.query_selector_all("p")
            deposit_text = None

            for p in paragraphs:
                label = p.query_selector("label")
                if label:
                    label_text = label.inner_text().strip().lower()
                    # Fix spelling typo and check label
                    if "desposit:" in label_text or "deposit:" in label_text:
                        deposit_text = p.inner_text().strip()
                        break

            if deposit_text and deposit_text.lower().startswith(("deposit:", "desposit:")) and any(
                    char.isdigit() for char in deposit_text):
                logger.info(f"✅ Account deposit successfully detected: {deposit_text}")
            else:
                logger.warning(f"⚠️ Unexpected deposit info text: {deposit_text}")

        except PlaywrightTimeoutError:
            logger.warning("⚠️ No deposit confirmation dialog appeared after confirming recharge.")
        except Exception as e:
            logger.exception(f"❌ Error verifying deposit success: {e}")

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
