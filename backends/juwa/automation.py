# automation_juwa.py
import json
import logging
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.juwa.config import *
from backends.juwa.utils.actions import click_recharge_for_account
from backends.juwa.utils.actions import click_redeem_for_account
from common.utils.aws_s3 import capture_and_upload_screenshot

from common.utils.logger import get_backend_logger
from common.utils.credential_utils import generate_credentials
from common.utils.ensure_directories import ensure_directories
from common.utils.handle_captcha import handle_captcha
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, \
    update_order_automation_status, update_automation_result, mark_freeplay_transferred, finalize_status, \
    mark_redeem_request_status
from common.utils.browser import with_persistent_browser

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):
    logger.info("Initiating login process.")
    logger.info("Fetching backend details from db...")

    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")
    logger.debug("Navigating to login page at: %s", LOGIN_URL)
    page.goto(login_url, wait_until="domcontentloaded")


    acct = page.locator(LOGIN_ACCOUNT)
    pwd = page.locator(LOGIN_PASSWORD)
    cap_in = page.locator(CAPTCHA_INPUT)
    login_btn = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")

        acct.fill(username)
        pwd.fill(password)

        if DEBUG:
            input("Debug mode: Solve CAPTCHA manually and press enter.")
        else:
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
                logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                page.reload(wait_until="domcontentloaded")
                continue
            cap_in.fill(text)
        login_btn.click()

        try:
            dialog_el = page.locator("div.el-message-box")
            dialog_el.wait_for(timeout=5000, state="visible")
            text = dialog_el.inner_text().strip().lower()
            if "the verification code is incorrect" in text:

                logger.warning("Incorrect CAPTCHA entered.")

                if not DEBUG:
                    solver.report_incorrect_image_captcha()
                page.reload(wait_until="domcontentloaded")
            elif "the user name or password is incorrect" in text:

                logger.error("Incorrect login credentials.")
                update_automation_result(task_id=task_id, status="failed", description=f"Incorrect login for {BACKEND_NAME}")
                raise Exception(f"Incorrect credentials for backend: {backend.name}")
            else:
                logger.info(f"Unknown dialog message: {text}")

                break
        except PlaywrightTimeoutError:
            logger.info("Login likely successful (no error dialog detected).")
            break

    logger.debug("Waiting for main page element after login.")
    logger.info("Login successful, navigating to user management page.")
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
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
                        logger.info("Account created successfully: %s", account_id)
                        save_credentials(account_id, password, logger, DATA_DIR)
                        insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                        success = True
                        break
                    else:
                        logger.warning(f"Unexpected message after creating account: {text}")
                        insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url), backend_id=BACKEND_ID)

            if success:
                break

            if should_restart:
                close_btn = page.locator(
                    ".el-dialog:has(.el-dialog__title:text('Essential information')) .el-dialog__headerbtn")
                if close_btn.is_visible():
                    close_btn.click()
                    logger.debug("Closed 'Essential information' dialog.")
                    continue
                else:
                    logger.debug("'Essential information' dialog close button not visible.")
                    page.got(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url), backend_id=BACKEND_ID)
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    recharge_inp = page.locator(
        "//label[text()='Recharge Amount']/following-sibling::div//input"
    )
    recharge_inp.wait_for(timeout=15_000)
    recharge_inp.fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

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
                    logger.error("Recharge failed: backend balance insufficient.")
                    update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                    return
                if "success" in text:
                    logger.info("Recharge successful.")
                    insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID)
                    update_order_automation_status(order_id, "finished")
                    update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
                else:
                    logger.warning(f"Unexpected recharge response: {msg}")
                    update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                    insert_log("warning", f"Unexpected recharge response: {msg}", source_url=str(page.url), backend_id=BACKEND_ID)

    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID)
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")


def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)
    page.wait_for_timeout(2_000)

    recharge_inp = page.locator(
        "//label[text()='Recharge Amount']/following-sibling::div//input"
    )
    recharge_inp.wait_for(timeout=15_000)
    recharge_inp.fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

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
                    logger.error("Recharge failed: backend balance insufficient.")
                    update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                    finalize_status(t, "failed", id_to_update)
                    return
                if "success" in text:
                    logger.info("Recharge successful.")
                    insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID)
                    update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
                    if t == "signup_freeplay":
                        mark_freeplay_transferred(account_id)
                    else:
                        finalize_status(t, "success", id_to_update)
                else:
                    logger.warning(f"Unexpected recharge response: {msg}")
                    update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                    insert_log("warning", f"Unexpected recharge response: {msg}", source_url=str(page.url), backend_id=BACKEND_ID)
                    finalize_status(t, "failed", id_to_update)

    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID)
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
        finalize_status(t, "failed", id_to_update)

def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    row = page.locator(
        "table.el-table__body tbody tr"
    ).filter(
        has=page.locator(f"td:nth-child(4) .cell:text('{account_id}')")
    ).first

    row.wait_for(timeout=5000)
    logger.debug("Account row located in table.")
    backend_account_id = row.locator("td:nth-child(3) .cell").inner_text().strip()
    data = {
        "id": row.locator("td:nth-child(3) .cell").inner_text().strip(),
        "account": row.locator("td:nth-child(4) .cell").inner_text().strip(),
        "balance": row.locator("td:nth-child(5) .cell").inner_text().strip(),
        "created_at": row.locator("td:nth-child(7) .cell").inner_text().strip(),
        "login_count": row.locator("td:nth-child(9) .cell").inner_text().strip(),
        "last_login": row.locator("td:nth-child(10) .cell").inner_text().strip(),
        "last_login_ip": row.locator("td:nth-child(11) .cell").inner_text().strip(),
    }
    update_game_id_by_username(account_id, backend_account_id)
    update_automation_result(task_id=task_id, status="success", description="Account information.", data=json.dumps(data))
    logger.info(f"Account read data: {data}")


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")

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

    if DEBUG:
        input("Debug mode: press enter to continue withdrawal.")

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
                    logger.error("Withdrawal failed due to insufficient gold.")
                    update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
                    mark_redeem_request_status(redeem_request_id, "failed")
                    return
                if "success" in text:
                    logger.info("Withdraw successful.")
                    insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID)
                    update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
                    mark_redeem_request_status(redeem_request_id, "processed")
                else:
                    logger.warning(f"Unexpected withdrawal response: {text}")
                    update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
                    mark_redeem_request_status(redeem_request_id, "failed")
                    insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url), backend_id=BACKEND_ID)
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdrawal on {BACKEND_NAME}")
        mark_redeem_request_status(redeem_request_id, "failed")
        logger.error("Failed to detect result dialog after account withdrawal.")
        insert_log("warning", "Failed to detect dialog after account withdrawal", source_url=str(page.url), backend_id=BACKEND_ID)



@with_persistent_browser
def action_create_account(page: Page, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    count = int(backend.accounts_creation_pd)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

    try:
        insert_log(
            "info",
            f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        _login_and_navigate(page, logger, backend, task_id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger)
            page.reload(wait_until="domcontentloaded")
        update_automation_result(task_id=task_id, status="success", description="Account creation successful.")
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account creation: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account creation: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account creation failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=BACKEND_ID)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        _login_and_navigate(page, logger, backend, task_id)
        _recharge_account(page, logger, count, account_id, order_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Error during account recharge. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t, id_to_update):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        _login_and_navigate(page, logger, backend, task_id)
        _freeplay_account(page, logger, count, account_id, task_id, t, id_to_update)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID)


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        _login_and_navigate(page, logger, backend, task_id)
        _withdraw_account(page, logger, count, account_id, task_id, redeem_request_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, description=f"Error during account withdrawal: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=BACKEND_ID)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=BACKEND_ID
        )
        _login_and_navigate(page, logger, backend, task_id)
        _read_account(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, description=f"Error during account read: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=BACKEND_ID)