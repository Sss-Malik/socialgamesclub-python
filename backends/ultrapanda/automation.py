# automation_ultrapanda.py
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.ultrapanda.config import *
from backends.ultrapanda.utils.credentials import generate_credentials
from backends.ultrapanda.utils.actions import click_set_score

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).fill(USERNAME)
    page.locator(LOGIN_PASSWORD).fill(PASSWORD)
    page.locator(LOGIN_BUTTON).click()

    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    logger.info("✅ Login successful.")

    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")


def _create_single_account(page: Page, logger: logging.Logger):
    page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    while True:
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials()
        logger.info("🔑 Generated credentials: %s / [REDACTED]", account_id)

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        ok_button = page.locator("div.el-form-item__content >> button.el-button--primary:has-text('OK')")
        ok_button.first.click()

        page.wait_for_timeout(1000)

        try:
            page.wait_for_selector("p.el-message__content", timeout=3000)
            messages = page.locator("p.el-message__content").all()

            should_restart = False
            success = False
            for msg in messages:
                if msg.is_visible():
                    text = msg.inner_text().strip().lower()
                    if "username used" in text or "form is being submitted" in text or "incorrect" in text:
                        logger.warning("⚠️ Detected message: %r — restarting account creation.", text)
                        should_restart = True
                        break
                    elif "sucessful" in text:
                        logger.info("✅ Account created successfully.")
                        save_credentials(account_id, password, logger, DATA_DIR)
                        success = True
                        break
                    else:
                        logger.info("ℹ️ Unhandled but visible message: %r", text)
            if success:
                break
            if should_restart:
                continue
        except PlaywrightTimeoutError:
            logger.warning("⚠️ Timeout occurred in account creation.")
            break


def _recharge_account(page: Page, logger: logging.Logger, points: int, account_id: str):
    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        page.locator("label.el-radio", has_text="Player account").click()

        page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
        page.locator("form.el-form.m-fm button:has-text('OK')").first.click()

        page.wait_for_timeout(1000)

        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=5_000)
            text = err.inner_text().strip().lower()
            if "error: 167" in text or "frequency of requests is too high" in text:
                logger.warning("⚠️ Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("✅ No error message, proceeding…")


        # open the score‐setting UI
        click_set_score(page, account_id, logger)

        # check for rate‐limit error
        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=5_000)
            text = err.inner_text().strip().lower()
            if "error: 167" in text or "frequency of requests is too high" in text:
                logger.warning("⚠️ Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("✅ No error message, proceeding…")

        # fill in points
        inp = page.locator('input[placeholder="Set points : ie 100"]')
        inp.wait_for(timeout=5_000, state="visible")
        inp.fill(str(points))

        if DEBUG:
            input("DEBUG: verify points then press Enter…")

        # confirm
        page.locator(
            "//div[contains(@class,'el-form-item__content') and .//span[text()='Cancel']]//span[text()='OK']"
        ).click()

        # wait for success confirmation
        try:
            alert = page.locator("div.el-message-box__message p, div.el-message.el-message--success p")
            alert.wait_for(state="visible", timeout=5_000)
            text = alert.inner_text().strip().lower()
            if "not authorized to check remaining balance" in text:
                logger.warning("Backend balance insufficient")
                return
            elif "sucessful operation" in text:
                logger.info("Account successfully recharged.")
        except PlaywrightTimeoutError:
            logger.warning("⚠️ No dialog appeared after setting score.")
        break



def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("==== Starting account creation (%d accounts) ====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("➡️ Creating account #%d of %d", i + 1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")
            browser.close()
    except PlaywrightTimeoutError as te:
        logger.exception("Timeout during recharge: %s", te)
    except Exception as e:
        logger.exception("🔥 Fatal error in account creation: %s", e)
    finally:
        logger.info("==== Finished account creation ====")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting score‐set action: %s → %d count =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except PlaywrightTimeoutError as te:
        logger.exception("Timeout during recharge: %s", te)
    except Exception as e:
        logger.exception("❌ Error during score‐set process: %s", e)
    finally:
        logger.info("===== Score‐set action completed =====")
