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
from settings import APP_ENV, HEADLESS, DEBUG


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).fill(USERNAME)
    page.locator(LOGIN_PASSWORD).fill(PASSWORD)
    page.locator(LOGIN_BUTTON).click()

    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=20_000)
    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
    logger.info("✅ Login and navigation successful.")



def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Initiating create account dialog.")

    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=15_000)

    page.locator(ACCOUNT_BALANCE).fill("0")
    page.locator(CREATE_ACCOUNT).click()

    # wait for feedback
    try:
        alert = page.locator(
            "div.alert.alert-error, div.alert.alert-success",
        )
        alert.wait_for(timeout=20_000, state="visible")
        text = alert.inner_text().strip().lower()
        if "successfully created" in text:
            account_id = alert.locator("b").nth(0).inner_text().strip()
            logger.info("✅ Account created successfully.")
            save_credentials(account_id, "null", logger, DATA_DIR)
        else:
            logger.warning("⚠️ Unexpected success message: %r", text)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No success message after creating account.")


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting recharge for account: {account_id} with count: {count}")

    acc_sr = page.locator(ACCOUNT_SEARCH_INPUT)
    acc_sr.wait_for(timeout=15_000)
    acc_sr.fill(account_id)
    page.locator('button:has-text("Search")').click()

    click_purchase_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    # wait for & fill deposit modal
    modal = page.locator("#modal-deposite")
    modal.wait_for(state="visible", timeout=15_000)
    amt_input = modal.locator("input#modal-deposite-amount")
    amt_input.wait_for(timeout=5_000)
    amt_input.fill(str(count))

    if DEBUG:
        input("Debug mode activated. Press enter to continue...")

    logger.debug("✅ Filled deposit amount with: %d", count)

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Purchase']").click()
    logger.debug("✅ Clicked Purchase button in modal.")

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
        if "not enough credits" in text:
            logger.error("⚠️ Account has insufficient credits.")
    elif alert.get_attribute("class").split().count("alert-success"):
        if "amount added" in text:
            logger.info("✅ Purchase completed successfully")
        else:
            logger.info("ℹ️ Purchase succeeded with message: %s", text)
    else:
        logger.warning("⚠️ Matched an alert, but unknown type: %s", text)


def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.debug(f"Reading account: {account_id}")

    acc_sr = page.locator(ACCOUNT_SEARCH_INPUT)
    acc_sr.wait_for(timeout=15_000)
    acc_sr.fill(account_id)
    page.locator('button:has-text("Search")').click()

    row = page.locator(
        "#table-accounts tbody tr[rel='account']"
    ).filter(
        has=page.locator(f"td:nth-child(2) span.label:text('{account_id}')")
    ).first

    row.wait_for(timeout=5000)

    data = {
        "account_number": row.locator("td:nth-child(2) span.label").inner_text().strip(),
        "username_notes": row.locator("td:nth-child(4)").inner_text().strip(),
        "created": row.locator("td:nth-child(5)").inner_text().strip(),
        "balance": row.locator("td:nth-child(6) code[rel='balance']").inner_text().strip(),
        "total_wins": row.locator("td:nth-child(6) code[rel='total_wins']").inner_text().strip(),
        "state": row.locator("td:nth-child(7) span[rel='online']").inner_text().strip(),
    }


    logger.info("✅ Extracted row data: %s", data)


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting withdraw for account: {account_id} with count: {count}")

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

    if DEBUG:
        input("Debug mode activated. Press enter to continue...")

    logger.debug("✅ Filled withdrawal amount with: %d", count)

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Redeem']").click()
    logger.debug("✅ Clicked Purchase button in modal.")

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
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("➡️ Creating account #%d of %d", i+1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
    finally:
        logger.info("Create-account action completed.")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting topup: account_id=%s | count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account recharge: %s", e, exc_info=True)
    finally:
        logger.info("Recharge-account action completed.")


def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting withdraw: account_id=%s | count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
    finally:
        logger.info("===== Withdraw-account action completed =====")

def action_read_account(account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting read: account_id=%s =====", account_id)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()

            _login_and_navigate(page, logger)
            _read_account(page, logger, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
    finally:
        logger.info("===== Read process finished =====")
