# automation_juwa.py
import logging
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.juwa.config import *
from backends.juwa.utils.actions import click_recharge_for_account
from backends.juwa.utils.actions import click_redeem_for_account

from common.utils.logger import get_backend_logger
from common.utils.credential_utils import generate_credentials
from common.utils.ensure_directories import ensure_directories
from common.utils.handle_captcha import handle_captcha
from common.utils.save_credentials import save_credentials


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd = page.locator(LOGIN_PASSWORD)
    cap_in = page.locator(CAPTCHA_INPUT)
    login_btn = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")

        acct.fill(USERNAME)
        pwd.fill(PASSWORD)

        logger.debug("Solving CAPTCHA…")
        text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
        if not text or text == 0:
            logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
            page.reload(wait_until="domcontentloaded")
            continue

        cap_in.fill(text)

        login_btn.click()

        try:
            page.locator("div.el-message-box", has_text="incorrect").wait_for(timeout=5000)
            logger.warning("❗ CAPTCHA incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.info("No CAPTCHA-error message, proceeding.")
            break

    logger.debug("Waiting for main page element after login.")
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Initiating create account dialog.")
    while True:
        page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials(BACKEND_SIGNATURE)
        logger.debug(f"Generated credentials: {account_id} / {password}")

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        page.locator(CONFIRM_PASSWORD).fill(password)
        page.locator(CREATE_ACCOUNT).click()

        page.wait_for_timeout(1000)

        try:
            page.wait_for_selector("p.el-message__content", timeout=3000)
            messages = page.locator("p.el-message__content").all()

            should_restart = False
            success = False
            for msg in messages:
                if msg.is_visible():
                    text = msg.inner_text().strip().lower()
                    if "login name have used" in text or "form is being submitted" in text or "incorrect" in text:
                        logger.warning("⚠️ Detected message: %r — restarting account creation.", text)
                        should_restart = True
                        break
                    elif "success" in text:
                        logger.info("✅ Account created successfully.")
                        save_credentials(account_id, password, logger, DATA_DIR)
                        success = True
                        break
                    else:
                        logger.info("ℹ️ Unhandled but visible message: %r", text)

            if success:
                break

            if should_restart:
                close_btn = page.locator(
                    ".el-dialog:has(.el-dialog__title:text('Essential information')) .el-dialog__headerbtn")
                if close_btn.is_visible():
                    close_btn.click()
                    logger.debug("🧹 Closed 'Essential information' dialog.")
                    continue
                else:
                    logger.debug("⚠️ 'Essential information' dialog close button not visible.")
                    page.got(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
        except PlaywrightTimeoutError:
            logger.exception("⚠️ Timeout occurred in account creation.")
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting recharge for account: {account_id} with count: {count}")

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    recharge_inp = page.locator(
        "//label[text()='Recharge Amount']/following-sibling::div//input"
    )
    recharge_inp.wait_for(timeout=15_000)
    recharge_inp.fill(str(count))


    dlg = page.locator(
        "div.el-dialog",
        has=page.locator("span.el-dialog__title", has_text="Please confirm your recharge & details!")
    )
    confirm_btn = dlg.locator(
        ".el-dialog__footer button.el-button--primary",
        has_text="Confirm"
    )
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    try:
        page.wait_for_selector("p.el-message__content", timeout=3000, state="attached")
        messages = page.locator("p.el-message__content").all()
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "not enougn balance" in text:
                    logger.error("Insufficient balance")
                    return
                if "success" in text:
                    logger.info(f"✅ Account deposit successfully detected: {text}")

    except PlaywrightTimeoutError:
        logger.info("No error messages detected. Assuming success")


def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.debug(f"Reading account: {account_id}")
    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    row = page.locator(
        "table.el-table__body tbody tr"
    ).filter(
        has=page.locator(f"td:nth-child(4) .cell:text('{account_id}')")
    ).first

    row.wait_for(timeout=5000)

    data = {
        "id": row.locator("td:nth-child(3) .cell").inner_text().strip(),
        "account": row.locator("td:nth-child(4) .cell").inner_text().strip(),
        "balance": row.locator("td:nth-child(5) .cell").inner_text().strip(),
        "created_at": row.locator("td:nth-child(7) .cell").inner_text().strip(),
        "login_count": row.locator("td:nth-child(9) .cell").inner_text().strip(),
        "last_login": row.locator("td:nth-child(10) .cell").inner_text().strip(),
        "last_login_ip": row.locator("td:nth-child(11) .cell").inner_text().strip(),
    }

    logger.info("✅ Extracted row data: %s", data)

def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting withdraw for account: {account_id} with count: {count}")

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_redeem_for_account(page, account_id, logger)
    dlg = page.locator(
        "div.el-dialog",
        has=page.locator("span.el-dialog__title", has_text="Please confirm your redeem & details!")
    )
    dlg.wait_for(timeout=15_000, state="visible")

    redeem_input = dlg.locator(
        "//label[text()='Redeem Amount']/following-sibling::div//input"
    )
    redeem_input.wait_for(timeout=15_000)
    redeem_input.fill(str(count))

    confirm_btn = dlg.locator(
        ".el-dialog__footer button.el-button--primary",
        has_text="Confirm"
    )
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    try:
        page.wait_for_selector("p.el-message__content", timeout=3000, state="attached")
        messages = page.locator("p.el-message__content").all()
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "the redeem amount can not be greater than the balance on the body！" in text:
                    logger.error("⚠️ Customer balance insufficient")
                    return
                if "success" in text:
                    logger.info(f"✅ Account withdraw successful")
                else:
                    logger.info(f"Unknown withdraw confirm message")

    except PlaywrightTimeoutError:
        logger.info("No error messages detected. Assuming success")



def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
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
                logger.info("Creating account #%d of %d", i + 1, count)
                _create_single_account(page, logger)
                page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
    finally:
        logger.info("Create-account action completed.")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
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
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
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
        logger.info("Withdraw-account action completed.")


def action_read_account(account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
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
        logger.info("Read-account action completed.")

