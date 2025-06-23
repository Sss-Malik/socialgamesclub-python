# automation_gamevault.py
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.handle_captcha import handle_captcha

from backends.gamevault.config import *
from backends.gamevault.utils.credentials import generate_credentials
from backends.gamevault.utils.actions import click_recharge_for_account
from backends.gamevault.utils.actions import click_redeem_for_account


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("🚀 Launching browser and navigating to %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap  = page.locator(CAPTCHA_INPUT)
    btn  = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt+1}")
        acct.fill(USERNAME)
        pwd.fill(PASSWORD)

        logger.debug("Solving CAPTCHA…")
        text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
        if not text or text == 0:
            page.reload(wait_until="domcontentloaded")
            continue

        cap.fill(text)
        if DEBUG:
            input("DEBUG: Manually complete CAPTCHA, then press Enter…")

        btn.click()

        try:
            page.locator("p.el-message__content", has_text="incorrect").wait_for(timeout=3_000)
            logger.warning("❗ CAPTCHA incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.info("CAPTCHA accepted.")
            break

    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    logger.info("✅ Logged in successfully.")


    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

def _create_single_account(page: Page, logger: logging.Logger):
    while True:
        logger.debug("Opening create‐account form")
        page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials()
        logger.info("🔑 Generated credentials: %s / [REDACTED]", account_id)

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        page.locator(CONFIRM_PASSWORD).fill(password)

        page.locator(CREATE_ACCOUNT).click()

        # Short pause to allow message(s) to render
        page.wait_for_timeout(1000)

        # Look for all visible message contents
        messages = page.locator("p.el-message__content").all()

        should_restart = False
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "login name have used" in text or "form is being submitted" in text:
                    logger.warning("⚠️ Detected message: %r — restarting account creation.", text)
                    should_restart = True
                    break

        if should_restart:
            close_btn = page.locator(
                ".el-dialog:has(.el-dialog__title:text('Essential information')) .el-dialog__headerbtn")
            if close_btn.is_visible():
                close_btn.click()
                logger.debug("🧹 Closed 'Essential information' dialog.")
            else:
                logger.warning("⚠️ 'Essential information' dialog close button not visible.")
            page.wait_for_timeout(1000)
            continue

        try:
            dlg = page.locator(".el-dialog:has(#invoiceModel)")
            dlg.wait_for(timeout=10000, state="visible")
            text = dlg.inner_text().strip().lower()
            if "successfully" in text:
                logger.info("✅ Account created successfully.")
                save_credentials(account_id, password, logger, DATA_DIR)
                break
            else:
                logger.warning("⚠️ Unexpected success message: %r", text)
        except PlaywrightTimeoutError:
            logger.warning("⚠️ No success dialog appeared after creating account.")



def _recharge_account(page: Page, logger: logging.Logger, amount: int, account_id: str):
    logger.debug("Searching for account to recharge: %s", account_id)
    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)

    # fill amount
    page.locator("//label[text()='Recharge Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("DEBUG: review recharge amount, then press Enter…")

    # confirm dialog
    dlg = page.locator("div.el-dialog",
                      has=page.locator("span.el-dialog__title", has_text="Please confirm your recharge & details!"))
    confirm_btn = dlg.locator(".el-dialog__footer button.el-button--primary", has_text="Confirm")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    messages = page.locator("p.el-message__content").all()
    for msg in messages:
        if msg.is_visible():
            text = msg.inner_text().strip().lower()
            if "not enougn balance" in text or "form is being submitted" in text:
                logger.warning("⚠️ Detected message: %r —", text)
                raise Exception("<UNK> Not enough enough balance.")

    # verify deposit
    try:
        invoice = page.locator("#invoiceModel")
        invoice.wait_for(timeout=10_000, state="visible")
        deposit = invoice.locator("p", has=page.locator("label", has_text="DEPOSIT:"))
        deposit.wait_for(timeout=5_000, state="visible")
        txt = deposit.inner_text().strip().lower()
        if txt.startswith("deposit:") and any(ch.isdigit() for ch in txt):
            logger.info(f"✅ Deposit confirmed: {txt}")
        else:
            logger.warning(f"⚠️ Unexpected deposit text: {txt}")
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No deposit confirmation appeared.")
    except Exception as e:
        logger.exception(f"❌ Error verifying deposit: {e}")


def _withdraw_account(page: Page, logger: logging.Logger, amount: int, account_id: str):
    logger.debug("Searching for account to withdraw: %s", account_id)
    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_redeem_for_account(page, account_id, logger)

    # confirm dialog
    dlg = page.locator("div.el-dialog",
                       has=page.locator("span.el-dialog__title", has_text="Please confirm your redeem & details!"))

    dlg.wait_for(timeout=10_000, state="visible")

    # fill amount
    dlg.locator("//label[text()='Redeem Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("DEBUG: review withdraw amount, then press Enter…")


    confirm_btn = dlg.locator(".el-dialog__footer button.el-button--primary", has_text="Confirm")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    # verify withdraw
    try:
        messages = page.locator("p.el-message__content").all()
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "the redeem amount can not be greater than the balance on the body！" in text:
                    logger.warning("⚠️ Customer balance insufficient")
                    return
                elif "success" in text:
                    logger.info(f"✅ Withdraw confirmed")
                else:
                    logger.warning(f"⚠️ Unexpected deposit text: {text}")

    except PlaywrightTimeoutError:
        logger.warning("⚠️ No deposit confirmation appeared.")


def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("==== Starting account creation (%d) ====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("🔨 Creating account #%d of %d", i+1, count)
                _create_single_account(page, logger)
                page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Fatal error in create‐account flow: %s", e)
    finally:
        logger.info("==== Finished account creation ====")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting recharge: %s → %d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during recharge flow: %s", e)
    finally:
        logger.info("===== Finished recharge process =====")



def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting recharge: %s → %d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during recharge flow: %s", e)
    finally:
        logger.info("===== Finished recharge process =====")
