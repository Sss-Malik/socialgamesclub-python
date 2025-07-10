import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.vblink.config import *
from backends.vblink.utils.credentials import generate_credentials
from backends.vblink.utils.actions import click_set_score

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_order_automation_status, update_automation_result
from common.utils.browser import with_browser

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):
    logger.info("Starting login process.")
    logger.debug("Fetching backend details from db...")
    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")
    logger.debug("Navigating to login page at: %s", LOGIN_URL)
    page.goto(login_url, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).fill(username)
    page.locator(LOGIN_PASSWORD).fill(password)

    if DEBUG:

        input("Debug mode: Solve CAPTCHA manually and press enter.")


    page.locator(LOGIN_BUTTON).click()

    try:
        dialog_el = page.locator("p.el-message__content")
        dialog_el.wait_for(timeout=5000, state="visible")
        text = dialog_el.inner_text().strip().lower()
        if "incorrect" in text:

            logger.error("Incorrect login credentials.")
            update_automation_result(task_id=task_id, status="failed", description=f"Incorrect login for {BACKEND_NAME}.")
            raise Exception(f"Incorrect credentials for backend: {backend.name}")
        else:
            logger.info(f"Unknown dialog message: {text}")
    except PlaywrightTimeoutError:
        logger.info("Login likely successful (no error dialog detected).")

    logger.info("Login successful, navigating to user management page.")

    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    logger.info("✅ Login successful.")

    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
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
                        logger.warning("Detected message: %r — restarting account creation.", text)
                        should_restart = True
                        break
                    elif "sucessful" in text:
                        logger.info("Account created successfully: %s", account_id)
                        insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                        save_credentials(account_id, password, logger, DATA_DIR)
                        success = True
                        break
                    else:
                        logger.warning(f"Unexpected message after creating account: {text}")
                        insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url))
            if success:
                break
            if should_restart:
                continue
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url))
            break


def _recharge_account(page: Page, logger: logging.Logger, points: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={points}")
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
                logger.warning("Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("No error message, proceeding…")


        # open the score‐setting UI
        click_set_score(page, account_id, logger)

        # check for rate‐limit error
        try:
            err = page.locator("p.el-message__content")
            err.wait_for(state="visible", timeout=5_000)
            text = err.inner_text().strip().lower()
            if "error: 167" in text or "frequency of requests is too high" in text:
                logger.warning("Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("No error message, proceeding…")

        # fill in points
        inp = page.locator('input[placeholder="Set points : ie 100"]')
        inp.wait_for(timeout=5_000, state="visible")
        inp.fill(str(points))

        if DEBUG:
            input("Debug mode: press enter to continue recharge.")

        # confirm
        page.locator(
            "//div[contains(@class,'el-form-item__content') and .//span[text()='Cancel']]//span[text()='OK']"
        ).click()

        # wait for success confirmation
        try:
            alert = page.locator("div.el-message-box__message p, div.el-message.el-message--success p")
            alert.wait_for(state="visible", timeout=25000)
            text = alert.inner_text().strip().lower()
            if "not authorized to check remaining balance" in text:
                logger.error("Recharge failed: backend balance insufficient.")
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
            elif "sucessful operation" in text:
                logger.info("Recharge successful.")
                insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
                update_order_automation_status(order_id, "finished")
                update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
            else:
                logger.warning(f"Unexpected recharge response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url))
        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
            update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
            insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url))
        break

def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
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
                logger.warning("Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("No error message, proceeding…")

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
        logger.debug("Account row located in table.")

        data = {
            "account": row.locator("td:nth-child(2) .cell span").inner_text().strip(),
            "balance": row.locator("td:nth-child(10) .cell span").inner_text().strip(),
        }

        update_automation_result(task_id=task_id, status="success", data=json.dumps(data), description="Account information.")

        logger.info(f"Account read data: {data}")
        break

def _withdraw_account(page: Page, logger: logging.Logger, points: int, account_id: str, task_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={points}")
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
                logger.warning("⚠Frequency too high, retrying…")
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
                logger.warning("Frequency too high, retrying…")
                continue
        except PlaywrightTimeoutError:
            logger.info("No error message, proceeding…")

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
                logger.error("Withdrawal failed due to insufficient gold.")
                update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
                raise Exception(f"Insufficient customer balance for withdrawal: {account_id}, backend: {BACKEND_NAME}")
            elif "sucessful operation" in text:
                logger.info("Withdraw successful.")
                update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
                insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url))
            else:
                logger.warning(f"Unexpected withdrawal response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
                insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url))
        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
            update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdrawal on {BACKEND_NAME}")
            insert_log("warning", f"Failed to detect dialog after withdraw for account: {account_id}", source_url=str(page.url))
        break


@with_browser
def action_create_account(page: Page, task_id):
    backend = get_backend(BACKEND_NAME)
    count = int(backend.accounts_creation_pd)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

    try:
        insert_log(
            "info",
            f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
        )
        _login_and_navigate(page, logger, backend, task_id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger)
            page.reload(wait_until="domcontentloaded")
        update_automation_result(task_id=task_id, status="success", description="Account creation successful.")
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account creation: {e}",
            source_url=str(page.url),
        )
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url))

@with_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
        )
        _login_and_navigate(page, logger, backend, task_id)
        _recharge_account(page, logger, count, account_id, order_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url),
        )
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url))

@with_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
        )
        _login_and_navigate(page, logger, backend, task_id)
        _withdraw_account(page, logger, count, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url),
        )
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url))

@with_browser
def action_read_account(page: Page, account_id: str, task_id):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url)
        )
        _login_and_navigate(page, logger, backend, task_id)
        _read_account(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url),
        )
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url))
