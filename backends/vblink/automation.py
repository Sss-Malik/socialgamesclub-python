import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.vblink.config import *
from backends.vblink.utils.credentials import generate_credentials
from backends.vblink.utils.actions import click_set_score

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("Fetching backend details from db...")
    backend = get_backend(BACKEND_NAME)
    username = backend.get("username") or USERNAME
    password = backend.get("password") or PASSWORD
    login_url = backend.get("backend_url") or LOGIN_URL

    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(login_url, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).fill(username)
    page.locator(LOGIN_PASSWORD).fill(password)
    page.locator(LOGIN_BUTTON).click()

    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    logger.info("✅ Login successful.")

    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Initiating create account dialog.")
    page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    while True:
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

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
    logger.debug(f"Starting recharge for account: {account_id} with count: {points}")
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
            input("Debug mode activated. Press enter to continue...")

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

def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.debug(f"Reading account: {account_id}")
    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        page.locator("label.el-radio", has_text="Player account").click()

        page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
        page.locator("form.el-form.m-fm button:has-text('OK')").first.click()

        page.wait_for_timeout(1000)

        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=2000)
            text = err.inner_text().strip().lower()
            if "error: 167" in text or "frequency of requests is too high" in text:
                logger.warning("⚠️ Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("✅ No error message, proceeding…")

        table = page.locator(
            "div.el-table",
            has=page.locator("th", has_text="Connect game provider UID")
        ).first

        table.wait_for(timeout=10000)

        row = table.locator(
            "tbody tr",
            has=page.locator("td:nth-child(2) .cell", has_text=account_id.lower())
        ).first

        row.wait_for(timeout=5000)

        data = {
            "account": row.locator("td:nth-child(2) .cell span").inner_text().strip(),
            "balance": row.locator("td:nth-child(10) .cell span").inner_text().strip(),
        }

        logger.info("✅ Extracted row data: %s", data)
        break

def _withdraw_account(page: Page, logger: logging.Logger, points: int, account_id: str):
    logger.debug(f"Starting withdraw for account: {account_id} with count: {points}")
    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        page.locator("label.el-radio", has_text="Player account").click()

        page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
        page.locator("form.el-form.m-fm button:has-text('OK')").first.click()

        page.wait_for_timeout(1000)

        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=2000)
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
            err.wait_for(state="visible", timeout=2000)
            text = err.inner_text().strip().lower()
            if "error: 167" in text or "frequency of requests is too high" in text:
                logger.warning("⚠️ Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("✅ No error message, proceeding…")

        # fill in points
        inp = page.locator('input[placeholder="Set points : ie 100"]')
        inp.wait_for(timeout=5_000, state="visible")
        inp.fill(f"-{str(points)}")

        if DEBUG:
            input("Debug mode activated. Press enter to continue...")

        # confirm
        page.locator(
            "//div[contains(@class,'el-form-item__content') and .//span[text()='Cancel']]//span[text()='OK']"
        ).click()

        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=3000)
            text = err.inner_text().strip().lower()
            if "cannot exceed current points" in text:
                logger.warning("⚠️ Customer balance insufficient")
                return
            elif "sucessful operation" in text:
                logger.info("Account successfully redeemed.")
                return
        except PlaywrightTimeoutError:
            logger.warning("⚠️ No dialog appeared after setting score.")
        break


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
                logger.info("➡️ Creating account #%d of %d", i + 1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")
            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
    finally:
        logger.info("Create-account action completed.")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting score‐set action: %s → %d count =====", account_id, count)

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
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting redeem action: %s → %d count =====", account_id, count)

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
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting read action: %s =====", account_id)

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
        logger.info("===== Read-account action completed =====")
