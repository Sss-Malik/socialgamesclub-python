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

from settings import APP_ENV, HEADLESS, DEBUG


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.debug("Navigating to login page at: %s", LOGIN_URL)

    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap  = page.locator(CAPTCHA_INPUT)
    btn  = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")

        acct.fill(USERNAME)
        pwd.fill(PASSWORD)

        logger.debug("Solving CAPTCHA…")
        if DEBUG:
            input("Debug mode activated. Press Enter to continue...")
        else:
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
                logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                page.reload(wait_until="domcontentloaded")
                continue

            cap.fill(text)
        btn.click()

        try:
            page.locator("p.el-message__content", has_text="incorrect").wait_for(timeout=5000)
            logger.warning("CAPTCHA incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.info("CAPTCHA accepted.")
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

        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        page.locator(CONFIRM_PASSWORD).fill(password)

        page.screenshot(path="headless_debug.png", full_page=True)

        page.locator(CREATE_ACCOUNT).click()

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
                logger.debug("Closed 'Essential information' dialog.")
            else:
                logger.debug("'Essential information' dialog close button not visible.")
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
                logger.warning("Unexpected success message: %r", text)
        except PlaywrightTimeoutError:
            logger.exception("No success dialog appeared after creating account.")


def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.debug(f"Reading account: {account_id}")
    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    row = page.locator(
        "table.el-table__body tbody tr"
    ).filter(
        has=page.locator(f"td .cell:text('{account_id}')")
    ).first

    row.wait_for(timeout=5000)

    data = {
        "id": row.locator("td:nth-child(2) .cell").inner_text().strip(),
        "account": row.locator("td:nth-child(4) .cell").inner_text().strip(),
        "balance": row.locator("td:nth-child(5) .cell").inner_text().strip(),
        "created_at": row.locator("td:nth-child(7) .cell").inner_text().strip(),
        "login_count": row.locator("td:nth-child(9) .cell").inner_text().strip(),
        "last_login": row.locator("td:nth-child(10) .cell").inner_text().strip(),
        "last_login_ip": row.locator("td:nth-child(11) .cell").inner_text().strip(),
    }

    logger.info("✅ Extracted row data: %s", data)



def _recharge_account(page: Page, logger: logging.Logger, amount: int, account_id: str):
    logger.debug(f"Starting recharge for account: {account_id} with count: {amount}")

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)

    # fill amount
    page.locator("//label[text()='Recharge Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("Debug mode activated. Press Enter to continue...")

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
                logger.warning("Detected message: %r —", text)
                return

    # verify deposit
    try:
        invoice = page.locator("#invoiceModel")
        invoice.wait_for(timeout=10_000, state="visible")
        deposit = invoice.locator("p", has=page.locator("label", has_text="DEPOSIT:"))
        deposit.wait_for(timeout=5_000, state="visible")
        txt = deposit.inner_text().strip().lower()
        if txt.startswith("deposit:") and any(ch.isdigit() for ch in txt):
            logger.info(f"✅ Recharge confirmed: {txt}")
        else:
            logger.warning(f"⚠️ Unexpected deposit text: {txt}")
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No deposit confirmation appeared.")
    except Exception as e:
        logger.exception(f"❌ Error verifying deposit: {e}")


def _withdraw_account(page: Page, logger: logging.Logger, amount: int, account_id: str):
    logger.debug(f"Starting withdraw for account: {account_id} with count: {amount}")

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
        input("Debug mode activated. Press Enter to continue...")

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
                    logger.warning(f"⚠️ Unexpected withdraw text: {text}")

    except PlaywrightTimeoutError:
        logger.exception("⚠️ No deposit confirmation appeared.")


def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

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
                logger.info("🔨 Creating account #%d of %d", i+1, count)
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
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

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
        logger.info("Withdraw-account action completed.")


def action_read_account(account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

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
        logger.info("Read-account action completed.")

