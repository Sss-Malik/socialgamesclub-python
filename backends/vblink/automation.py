import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.vblink.config import *
from backends.vblink.utils.credentials import generate_credentials
from backends.vblink.utils.actions import click_set_score, click_edit
from common.utils.aws_s3 import capture_and_upload_screenshot
from common.utils.emails import send_email
import random
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.otp import generate_2fa_code
from common.utils.poll_utils import wait_for_valid_session, wait_for_active_tasks_to_zero
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_order_automation_status, \
    update_automation_result, mark_freeplay_transferred, increment_active_tasks_count, decrement_active_tasks_count, \
    invalidate_latest_session, create_backend_session, finalize_status, mark_redeem_request_status, get_backend_account, \
    mark_bonus_transferred, update_password_by_username
from common.utils.browser import with_persistent_browser
from backends.ultrapanda.utils.session import inject_session_token, validate_session_token
from common.utils.redis_utils import acquire_login_lock, release_login_lock

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):

    page.goto(backend.backend_url, wait_until="domcontentloaded")

    page.locator(LOGIN_ACCOUNT).wait_for(timeout=20000)

    session = wait_for_valid_session(backend.name, logger)

    if session:
        logger.info("Valid session found, attempting to inject...")
        inject_session_token(page, session.token)

        if validate_session_token(page, logger):

            logger.info("Session injection and validation successful")
            page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
            try:
                dialog = page.locator("div[role='dialog'] >> text=Dear Customers,")
                dialog.wait_for(state="visible", timeout=5000)
                confirm_btn = page.locator("div[role='dialog'] button:has-text('confirm')")
                confirm_btn.click()
            except PlaywrightTimeoutError:
                pass
            page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
            return session

        else:
            logger.warning("Session injection failed. Invalidating session.")
            if wait_for_active_tasks_to_zero(session.id, logger=logger):
                logger.info("Session is now free, invalidating it.")
                invalidate_latest_session(backend.name)
            else:
                update_automation_result(task_id=task_id, status="failed",
                                         description="Session still in use. Aborting to avoid conflicts")
                raise Exception("Session still in use after waiting. Aborting to avoid conflicts")


    logger.info("No valid session. Attempting to acquire login lock.")
    if acquire_login_lock(backend.name):
        try:
            logger.info("Lock acquired. Proceeding with login.")

            page.goto(backend.backend_url, wait_until="domcontentloaded")

            username = backend.username or USERNAME
            password = backend.password or PASSWORD

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
                    update_automation_result(task_id=task_id, status="failed",
                                             description=f"Incorrect login for {BACKEND_NAME}.")
                    raise Exception(f"Incorrect credentials for backend: {backend.name}")
                else:
                    logger.info(f"Unknown dialog message: {text}")
            except PlaywrightTimeoutError:
                logger.info("Login likely successful (no error dialog detected).")


            try:
                auth_dialog = page.locator("div[role='dialog'] .dialog-title:text('Verify your identity')")
                auth_dialog.wait_for(state="visible", timeout=5000)
                logger.info("2FA detected. Solving")
                input_box = page.locator("input[placeholder='Verification code']")

                code = generate_2fa_code(secret_key=backend.binding_key)

                input_box.fill(code)

                ok_button = auth_dialog.locator("button:has-text('OK')")
                ok_button.click()
            except PlaywrightTimeoutError:
                pass

            page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
            logger.info("Login successful, navigating to user management page.")
            try:
                dialog = page.locator("div[role='dialog'] >> text=Dear Customers,")
                dialog.wait_for(state="visible", timeout=5000)
                confirm_btn = page.locator("div[role='dialog'] button:has-text('confirm')")
                confirm_btn.click()
            except PlaywrightTimeoutError:
                pass

            token = page.evaluate("() => sessionStorage.getItem('Admin-Token')")
            new_session = create_backend_session(backend.name, token=token)


            page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
            logger.info("Login and navigation successful.")

            return new_session
        finally:
            release_login_lock(backend.name)
    else:
        logger.info("Another task is logging in. Waiting for session...")
        session = wait_for_valid_session(backend.name, logger, timeout=40, interval=2)
        if not session:
            logger.error("Timeout waiting for session from another task")
            update_automation_result(task_id=task_id, status="failed",
                                     description="Timeout waiting for session from another task")
            raise Exception("Timeout waiting for session")
        inject_session_token(page, session.token)
        if not validate_session_token(page, logger):
            update_automation_result(task_id=task_id, status="failed", description="Session after wait was invalid")
            raise Exception("Session after wait was invalid")


        logger.info("Session from another task injected and validated.")
        page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
        try:
            dialog = page.locator("div[role='dialog'] >> text=Dear Customers,")
            dialog.wait_for(state="visible", timeout=5000)
            confirm_btn = page.locator("div[role='dialog'] button:has-text('confirm')")
            confirm_btn.click()
        except PlaywrightTimeoutError:
            pass
        page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

        logger.info("Session from another task injected and validated.")
        return session

def _create_single_account(page: Page, logger: logging.Logger, task_id):
    logger.debug("Opening create account dialog.")
    page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    while True:
        delay = random.randint(1000, 6000)
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        page.wait_for_timeout(delay)
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
                        insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
            if success:
                break
            if should_restart:
                continue
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
            break


def _recharge_account(page: Page, logger: logging.Logger, points: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)

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
                send_email(
                    subject="Recharge failed",
                    body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
                )
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                insert_log("error", description="Backend balance insufficient", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                update_order_automation_status(order_id, "failed")
                return
            elif "sucessful operation" in text:
                logger.info("Recharge successful.")
                insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                update_order_automation_status(order_id, "finished")
                update_automation_result(task_id=task_id, status="success", description="Recharge successful.")

                if _.user.bonus_received:
                    mark_bonus_transferred(account_id)

            else:
                logger.warning(f"Unexpected recharge response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                update_order_automation_status(order_id, "failed")
                insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
            update_order_automation_status(order_id, "failed")
            update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
            insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        break


def _freeplay_account(page: Page, logger: logging.Logger, points: int, account_id: str, task_id, t, id_to_update):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)

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
                insert_log("error", description="Backend balance insufficient", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                send_email(
                    subject="Recharge failed",
                    body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
                )
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                return
            elif "sucessful operation" in text:
                logger.info("Recharge successful.")
                insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
                if t == "signup_freeplay":
                    mark_freeplay_transferred(account_id)
                else:
                    finalize_status(t, True, id_to_update)
            else:
                logger.warning(f"Unexpected recharge response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
            update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
            insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
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

def _withdraw_account(page: Page, logger: logging.Logger, points: int, account_id: str, task_id, redeem_request_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)

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
                insert_log("error", description="Insufficient customer balance", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                mark_redeem_request_status(redeem_request_id, "failed")
                return
            elif "sucessful operation" in text:
                logger.info("Withdraw successful.")
                update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
                mark_redeem_request_status(redeem_request_id, "processed")
                insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            else:
                logger.warning(f"Unexpected withdrawal response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
                mark_redeem_request_status(redeem_request_id, "failed")
                insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
            update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdrawal on {BACKEND_NAME}")
            mark_redeem_request_status(redeem_request_id, "failed")
            insert_log("warning", f"Failed to detect dialog after withdraw for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        break



def _reset_password(page: Page, logger: logging.Logger, account_id: str, task_id: str):
    logger.info(f"Initiating reset password: account_id={account_id}")
    _ = get_backend_account(account_id)
    __, password = generate_credentials()

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

        # open the score‐setting UI
        click_edit(page, account_id, logger)

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
        inp = page.locator('input[placeholder="Length must be 6-16 characters! Must include a combination of numbers and letters, and allows some special characters: !@#$%^/.,()"]')
        inp.wait_for(timeout=5_000, state="visible")
        inp.fill(password)
        if DEBUG:
            input("Debug mode activated. Press enter to continue...")

        ok_btn = page.get_by_role("button", name="OK").nth(1)
        ok_btn.click()

        msg_box = page.locator(".el-message-box:visible")
        msg_box.wait_for(state="visible", timeout=10000)
        ok_btn = msg_box.get_by_role("button", name="OK")
        ok_btn.click()

        try:
            result = page.locator("p.el-message__content")
            result.wait_for(state="visible", timeout=5000)
            text = result.inner_text().strip().lower()
            if "sucessful operation" in text:
                logger.info("Password reset successful.")
                insert_log("info", description=f"Password reset successful for account {account_id}", source_url=str(page.url),
                           backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                update_automation_result(task_id=task_id, description="Password reset successful.", status="success",
                                         data=json.dumps({"password": password}))
                update_password_by_username(username=account_id, new_password=password)
            else:
                logger.warning(f"Password reset failed. Unhandled reset response: {text}")
                insert_log("error", description=f"Password reset failed. Unhandled reset response: {text}",
                           source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
                update_automation_result(task_id=task_id,
                                         description=f"Password reset failed. Unhandled reset response: {text}",
                                         status="failed")
        except PlaywrightTimeoutError:
            logger.warning("Password reset failed. Failed to detect result after reset")
            insert_log("error", description="Failed to detect reset response", source_url=str(page.url), backend_id=BACKEND_ID,
                       account_id=_.id, task_id=task_id)
            update_automation_result(task_id=task_id, description="Failed to detect reset response", status="failed")
        break


@with_persistent_browser
def action_create_account(page: Page, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    count = int(backend.accounts_creation_pd)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger, task_id)
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
        send_email(
            subject="Account creation failed",
            body=f"Critical error occurred during account creation for backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account creation: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Account creation failed. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during account recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, backend, task_id, t, id_to_update):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during freeplay recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
        send_email(
            subject="Account withdrawal failed",
            body=f"Critical error occurred during account withdrawal for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account withdrawal failed. {e}", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
        send_email(
            subject="Account read failed",
            body=f"Critical error occurred during reading account {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Account read failed. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)



@with_persistent_browser
def action_reset_password(page: Page, account_id: str, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Reset-password action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating password reset for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url),
            backend_id=backend.id, account_id=_.id, task_id=task_id
        )
        _login_and_navigate(page, logger, backend, task_id)
        _reset_password(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account password reset: %s", e, exc_info=True)
        send_email(
            subject="Account password reset failed",
            body=f"Critical error occurred during reset password for {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account password reset: {e}",
            source_url=str(page.url),
            backend_id=backend.id,
            account_id=_.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account password reset.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Reset-password action completed.")
        insert_log("info", "Reset password action completed", source_url=str(page.url), backend_id=backend.id, account_id=_.id, task_id=task_id)