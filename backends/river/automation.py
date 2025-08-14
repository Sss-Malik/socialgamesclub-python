# automation_river.py
import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
import random
from backends.river.config import *
from backends.river.utils.credentials import generate_credentials
from backends.river.utils.actions import click_purchase_for_account
from backends.river.utils.actions import click_redeem_for_account
from common.utils.aws_s3 import capture_and_upload_screenshot
from common.utils.emails import send_email

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_order_automation_status, \
    update_automation_result, mark_freeplay_transferred, finalize_status, mark_redeem_request_status, \
    get_backend_account, mark_bonus_transferred
from common.utils.browser import with_persistent_browser
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
    
    try:
        page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=1000)
        logger.info("Existing session detected; skipping login.")
        page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
        return
    except PlaywrightTimeoutError:
        logger.info("No existing session; proceeding with login.")


    page.locator(LOGIN_ACCOUNT).fill(username)
    page.locator(LOGIN_PASSWORD).fill(password)
    if DEBUG:
        input("Debug mode activated. Press Enter to continue...")
    page.locator(LOGIN_BUTTON).click()

    try:
        alert = page.locator("div.alert.alert-error")
        alert.wait_for(timeout=8000, state="visible")
        text = alert.inner_text().strip().lower()
        if "incorrect login or password" in text:
            update_automation_result(task_id=task_id, status="failed", description=f"Incorrect login for {BACKEND_NAME}.")
            logger.error("Incorrect login credentials.")
            raise Exception(f"Incorrect credentials for backend: {backend.name}")
    except PlaywrightTimeoutError:
        logger.info("Login likely successful (no error dialog detected).")

    logger.info("Login successful, navigating to user management page.")

    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=20_000)
    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
    logger.info("Login and navigation successful.")



def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
    delay = random.randint(1000, 6000)
    page.locator(CREATE_ACCOUNT_INIT).wait_for(timeout=15_000)

    page.locator(ACCOUNT_BALANCE).fill("0")
    page.wait_for_timeout(delay)
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
            logger.info("Account created successfully: %s", account_id)
            insert_backend_account(username=account_id, password="NULL", backend_id=BACKEND_ID)
            page.wait_for_timeout(delay)
            save_credentials(account_id, "null", logger, DATA_DIR)
        else:
            logger.warning(f"Unexpected message after creating account: {text}")
            insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url), backend_id=BACKEND_ID)
            page.wait_for_timeout(delay)
    except PlaywrightTimeoutError:
        logger.error("Failed to detect result dialog after account creation.")
        insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url), backend_id=BACKEND_ID)


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

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
        input("Debug mode: press enter to continue recharge.")

    logger.debug("✅ Filled deposit amount with: %d", count)

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Purchase']").click()
    logger.debug("Clicked Purchase button in modal.")

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass


    try:
        alert = page.wait_for_selector(
            "div.alert.alert-error, div.alert.alert-success",
            timeout=15_000,
            state="visible"
        )

        text = alert.inner_text().strip().lower()

        # 3) Branch on which one it is
        if alert.get_attribute("class").split().count("alert-error"):
            if "not enough credits" in text:
                logger.error("Recharge failed: backend balance insufficient.")
                send_email(
                    subject="Recharge failed",
                    body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
                )
                insert_log("error", description="Backend balance insufficient", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                update_order_automation_status(order_id, "failed")
                return
        elif alert.get_attribute("class").split().count("alert-success"):
            if "amount added" in text:
                logger.info("Recharge successful.")
                insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
                update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
                update_order_automation_status(order_id, "finished")

                if _.user.bonus_received:
                    mark_bonus_transferred(account_id)

            else:
                logger.warning(f"Unexpected recharge response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                update_order_automation_status(order_id, "failed")
                insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
        else:
            logger.warning("Matched an alert, but unknown type: %s", text)
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected alert detected on {BACKEND_NAME}")
            insert_log("warning", description="Unexpected alert detected on {BACKEND_NAME}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_order_automation_status(order_id, "failed")
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}.")
        insert_log("error", description="Failed to detect result after recharge", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
        update_order_automation_status(order_id, "failed")

def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

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
        input("Debug mode: press enter to continue recharge.")

    logger.debug("✅ Filled deposit amount with: %d", count)

    # click Purchase
    modal.locator("input.btn.btn-primary[type='submit'][value='Purchase']").click()
    logger.debug("Clicked Purchase button in modal.")

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass


    try:
        alert = page.wait_for_selector(
            "div.alert.alert-error, div.alert.alert-success",
            timeout=15_000,
            state="visible"
        )

        text = alert.inner_text().strip().lower()

        # 3) Branch on which one it is
        if alert.get_attribute("class").split().count("alert-error"):
            if "not enough credits" in text:
                logger.error("Recharge failed: backend balance insufficient.")
                send_email(
                    subject="Recharge failed",
                    body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
                )
                insert_log("error", description="Backend balance insufficient", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                return
        elif alert.get_attribute("class").split().count("alert-success"):
            if "amount added" in text:
                logger.info("Recharge successful.")
                insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
                update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
                if t == "signup_freeplay":
                    mark_freeplay_transferred(account_id)
                else:
                    finalize_status(t, True, id_to_update)
            else:
                logger.warning(f"Unexpected recharge response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
                insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
        else:
            logger.warning("Matched an alert, but unknown type: %s", text)
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected alert detected on {BACKEND_NAME}")
            insert_log("warning", f"Unexpected alert detected on {BACKEND_NAME}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)

    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)

def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")

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
    logger.debug("Account row located in table.")

    data = {
        "account_number": row.locator("td:nth-child(2) span.label").inner_text().strip(),
        "username_notes": row.locator("td:nth-child(4)").inner_text().strip(),
        "created": row.locator("td:nth-child(5)").inner_text().strip(),
        "balance": row.locator("td:nth-child(6) code[rel='balance']").inner_text().strip(),
        "total_wins": row.locator("td:nth-child(6) code[rel='total_wins']").inner_text().strip(),
        "state": row.locator("td:nth-child(7) span[rel='online']").inner_text().strip(),
    }

    logger.info(f"Account read data: {data}")
    update_automation_result(task_id=task_id, status="success", data=json.dumps(data), description="Account information.")


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

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

    try:
        alert = page.wait_for_selector(
            "div.alert.alert-error, div.alert.alert-success",
            timeout=15_000,
            state="visible"
        )

        text = alert.inner_text().strip().lower()

        # 3) Branch on which one it is
        if alert.get_attribute("class").split().count("alert-error"):
            if "not enough credits" in text:
                logger.error("Withdrawal failed due to insufficient gold.")
                update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
                insert_log("info", "insufficient customer balance", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
                mark_redeem_request_status(redeem_request_id, "failed")
                return
        elif alert.get_attribute("class").split().count("alert-success"):
            if "amount added" in text:
                logger.info("Withdraw successful.")
                update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
                mark_redeem_request_status(redeem_request_id, "processed")
                insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            else:
                logger.warning(f"Unexpected withdrawal response: {text}")
                update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
                mark_redeem_request_status(redeem_request_id, "failed")
                insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
        else:
            # in the extremely unlikely event it matched neither class…
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected alert detected on {BACKEND_NAME}.")
            insert_log("warning", f"Unexpected alert detected on {BACKEND_NAME}.", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            mark_redeem_request_status(redeem_request_id, "failed")
            logger.warning("⚠️ Matched an alert, but unknown type: %s", text)
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdraw on {BACKEND_NAME}.")
        insert_log("warning", f"Failed to detect result after withdraw on {BACKEND_NAME}", source_url=str(page.url),
                   backend_id=BACKEND_ID, account_id=_.id)
        mark_redeem_request_status(redeem_request_id, "failed")


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
            task_id=task_id
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
            source_url=str(page.url), backend_id=BACKEND_ID,
        )
        update_automation_result(task_id=task_id, description=f"Account creation failed. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=BACKEND_ID)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
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
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during account recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t, id_to_update):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
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
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during freeplay recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
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
        send_email(
            subject="Account withdrawal failed",
            body=f"Critical error occurred during account withdrawal for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account withdrawal failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
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
        send_email(
            subject="Account read failed",
            body=f"Critical error occurred during reading account {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id
        )
        update_automation_result(task_id=task_id, description=f"Account read failed. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
