# automation_river.py
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.river.config import *
from backends.river.utils.credentials import generate_credentials
from backends.river.utils.actions import click_purchase_for_account
from backends.river.utils.actions import click_redeem_for_account

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("🚀 Navigating to login page: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).fill(USERNAME)
    page.locator(LOGIN_PASSWORD).fill(PASSWORD)
    page.locator(LOGIN_BUTTON).click()

    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=20_000)
    logger.info("✅ Login successful.")

    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")


def _create_single_account(page: Page, logger: logging.Logger):
    # open the “create account” form
    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=15_000)

    # generate creds
    account_id, password = generate_credentials()
    logger.info("🔑 Generated credentials: %s / [REDACTED]", account_id)

    # fill & submit
    page.locator(ACCOUNT_ID).fill(account_id)
    page.locator(ACCOUNT_BALANCE).fill("0")
    page.locator(CREATE_ACCOUNT).click()

    # wait for feedback
    try:
        alert = page.locator(ACCOUNT_SUCCESS).first
        alert.wait_for(state="visible", timeout=3_000)
        text = alert.inner_text().strip().lower()
        if "successfully created" in text:
            logger.info("✅ Account created successfully.")
            save_credentials(account_id, password, logger, DATA_DIR)
        else:
            logger.warning("⚠️ Unexpected success message: %r", text)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No success message after creating account.")


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # search for user
    acc_sr = page.locator(ACCOUNT_SEARCH_INPUT)
    acc_sr.wait_for(timeout=15_000)
    acc_sr.fill(account_id)
    page.locator('button:has-text("Search")').click()

    # delegate to existing helper
    click_purchase_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    # wait for & fill deposit modal
    modal = page.locator("#modal-deposite")
    modal.wait_for(state="visible", timeout=15_000)
    amt_input = modal.locator("input#modal-deposite-amount")
    amt_input.wait_for(timeout=5_000)
    amt_input.fill(str(count))
    logger.info("✅ Filled deposit amount with: %d", count)

    # debug pause
    input("🔍 Press Enter to continue (e.g., after verifying inputs)…")

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Purchase']").click()
    logger.info("✅ Clicked Purchase button in modal.")

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass

    alert = page.wait_for_selector(
        "div.alert.alert-error, div.alert.alert-success",
        timeout=15_000,
        state="visible"
    )

    text = alert.inner_text().strip().lower()

    # 3) Branch on which one it is
    if alert.get_attribute("class").split().count("alert-error"):
        logger.error("❌ Purchase failed: %s", text)
        if "not enough credits" in text:
            logger.info("⚠️ Account has insufficient credits.")
    elif alert.get_attribute("class").split().count("alert-success"):
        if "amount added" in text:
            logger.info("✅ Purchase completed successfully: %s", text)
        else:
            logger.info("ℹ️ Purchase succeeded with message: %s", text)
    else:
        # in the extremely unlikely event it matched neither class…
        logger.warning("⚠️ Matched an alert, but unknown type: %s", text)



def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # search for user
    acc_sr = page.locator(ACCOUNT_SEARCH_INPUT)
    acc_sr.wait_for(timeout=15_000)
    acc_sr.fill(account_id)
    page.locator('button:has-text("Search")').click()

    # delegate to existing helper
    click_redeem_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    # wait for & fill deposit modal
    modal = page.locator("#modal-withdrawal")
    modal.wait_for(state="visible", timeout=15_000)
    amt_input = modal.locator("input#modal-withdrawal-amount")
    amt_input.wait_for(timeout=5_000)
    amt_input.fill(str(count))
    logger.info("✅ Filled withdrawal amount with: %d", count)

    # debug pause
    input("🔍 Press Enter to continue (e.g., after verifying inputs)…")

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Redeem']").click()
    logger.info("✅ Clicked Purchase button in modal.")

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass

    alert = page.wait_for_selector(
        "div.alert.alert-error, div.alert.alert-success",
        timeout=15_000,
        state="visible"
    )

    text = alert.inner_text().strip().lower()

    # 3) Branch on which one it is
    if alert.get_attribute("class").split().count("alert-error"):
        logger.error("❌ Redeem failed: %s", text)
        if "not enough credits" in text:
            logger.info("⚠️ Customer has insufficient credits.")
    elif alert.get_attribute("class").split().count("alert-success"):
        if "amount added" in text:
            logger.info("✅ Purchase completed successfully: %s", text)
        else:
            logger.info("ℹ️ Purchase succeeded with message: %s", text)
    else:
        # in the extremely unlikely event it matched neither class…
        logger.warning("⚠️ Matched an alert, but unknown type: %s", text)


def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("==== Starting account creation (%d accounts) ====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("➡️ Creating account #%d of %d", i+1, count)
                try:
                    _create_single_account(page, logger)
                except Exception as e:
                    logger.exception("❌ Error creating account #%d: %s", i+1, e)

                # reload back to management page
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception as e:
                    logger.warning("⚠️ Failed to reload page: %s", e)

            browser.close()
    except Exception as e:
        logger.exception("🔥 Fatal error in account creation: %s", e)
    finally:
        logger.info("==== Finished account creation ====")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting topup: account_id=%s | count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except Exception as e:
        logger.exception("🔥 Error during recharge process: %s", e)
    finally:
        logger.info("===== Topup process finished =====")


def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting withdraw: account_id=%s | count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except Exception as e:
        logger.exception("🔥 Error during withdraw process: %s", e)
    finally:
        logger.info("===== Withdraw process finished =====")
