import logging
import json
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.firekirin.config import *
from backends.firekirin.utils.credentials import generate_credentials
from backends.firekirin.utils.actions import click_update_for_account

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, \
    update_order_automation_status, update_automation_result, mark_freeplay_transferred, finalize_status, \
    mark_redeem_request_status, get_backend_account, mark_bonus_transferred
from common.utils.browser import with_persistent_browser
from common.utils.aws_s3 import capture_and_upload_screenshot
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
        if page.locator(MAIN_PAGE_EL).is_visible(timeout=7000):
            logger.info("Existing session detected; skipping login.")
            page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=20_000)
            return
    except PlaywrightTimeoutError:
        logger.info("No existing session; proceeding with login.")

    account_input = page.locator(LOGIN_ACCOUNT)
    password_input = page.locator(LOGIN_PASSWORD)
    captcha_input = page.locator(CAPTCHA_INPUT)
    login_button = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")
        account_input.fill(username)
        password_input.fill(password)

        logger.debug("Solving CAPTCHA…")
        if DEBUG:
            input("Debug mode: Solve CAPTCHA manually and press enter.")
        else:
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
                logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                page.reload(wait_until="domcontentloaded")
                continue
            captcha_input.fill(text)

        login_button.click()

        try:
            dialog_el = page.locator("div#mb_con", has_text="incorrect")
            dialog_el.wait_for(timeout=10000, state="visible")
            text = dialog_el.inner_text().strip().lower()
            if "the validation code you filled in is incorrect" in text:

                logger.warning("Incorrect CAPTCHA entered.")

                if not DEBUG:
                    solver.report_incorrect_image_captcha()
                page.locator("input#mb_btn_ok").click()
                page.wait_for_load_state("networkidle")
                continue
            elif "the account or password you filled in is incorrect" in text:

                logger.error("Incorrect login credentials.")
                update_automation_result(task_id=task_id, description=f"Incorrect login credentials for {BACKEND_NAME}.", status="failed")
                raise Exception(f"Incorrect login credentials for backend: {backend.name}")
            else:
                logger.info(f"Unknown dialog message: {text}")
                break
        except PlaywrightTimeoutError:
            logger.info("Login likely successful (no error dialog detected).")

            break

    logger.info("Login successful, navigating to user management page.")
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=20_000)
    logger.info("Login and navigation successful.")

def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
    page.frame_locator(MAIN_IFRAME).locator(CREATE_ACCOUNT_INIT).click(timeout=20_000)
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")
        dialog.locator(ACCOUNT_ID).fill(account_id)
        dialog.locator(ACCOUNT_PASSWORD).fill(password)
        dialog.locator(CONFIRM_PASSWORD).fill(password)
        dialog.locator(CREATE_ACCOUNT).click()

        try:
            result = page.locator("#mb_con")
            result.wait_for(timeout=25000, state="visible")
            message = result.inner_text().strip().lower()

            if "already exists" in message:
                logger.warning(f"Account ID already exists: {account_id}")
                page.locator("#mb_btn_ok").click()
                continue
            elif "success" in message:
                logger.info("Account created successfully: %s", account_id)
                insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                save_credentials(account_id, password, logger, DATA_DIR)
                page.locator("#mb_btn_ok").click()
                break
            elif "too frequent" in message:
                logger.warning("automation detected. Aborting...")
                insert_log("warning", "Automation script detected. Aborting for now", source_url=str(page.url), backend_id=BACKEND_ID)
                raise Exception("Automation script detected. Aborting...")
            else:
                logger.warning(f"Unexpected message after creating account: {message}")
                insert_log("warning", f"Unexpected create account response: {message}", source_url=str(page.url), backend_id=BACKEND_ID)
                break
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url), backend_id=BACKEND_ID)
            break



def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Recharge").click()

    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_order_automation_status(order_id, "finished")
            update_automation_result(task_id=task_id, status="success", description="Recharge successful")

            if _.user.bonus_received:
                mark_bonus_transferred(account_id)

        elif "insufficient" in text:
            logger.error("Recharge failed: backend balance insufficient.")
            update_order_automation_status(order_id, "failed")
            insert_log("warning", "Backend balance insufficient", source_url=str(page.url),
                       backend_id=BACKEND_ID, account_id=_.id)
            update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance for {BACKEND_NAME}")
            return
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_order_automation_status(order_id, "failed")
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
    except PlaywrightTimeoutError:
        update_order_automation_status(order_id, "failed")
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)



def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Recharge").click()

    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_automation_result(task_id=task_id, status="success", description="Recharge successful")
            if t == "signup_freeplay":
                mark_freeplay_transferred(account_id)
            else:
                finalize_status(t, True, id_to_update)
        elif "insufficient" in text:
            logger.error("Recharge failed: backend balance insufficient.")
            insert_log("warning", "Backend balance insufficient", source_url=str(page.url),
                       backend_id=BACKEND_ID, account_id=_.id)
            update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance for {BACKEND_NAME}")
            return
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Redeem").click()

    redeem = page.frame_locator('iframe[src*="AccountManager"]')
    customer_balance = redeem.locator('input#txtLeScore').get_attribute('value')
    logger.debug(f"Customer balance: {customer_balance}")

    if count > float(customer_balance):
        logger.error("Withdraw failed: insufficient customer balance.")
        update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
        insert_log("warning", "Insufficient customer", source_url=str(page.url),
                   backend_id=BACKEND_ID, account_id=_.id)
        mark_redeem_request_status(redeem_request_id, "failed")
        return

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated; press enter to continue...")

    redeem.locator('input[type="button"][value="Redeem"]').click()

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Withdraw successful.")
            insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
            mark_redeem_request_status(redeem_request_id, "processed")
        elif "not enough gold" in text:
            logger.error("Withdrawal failed due to insufficient gold.")
            update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
            insert_log("warning", "Insufficient customer", source_url=str(page.url),
                       backend_id=BACKEND_ID, account_id=_.id)
            mark_redeem_request_status(redeem_request_id, "failed")
            return
        else:
            logger.warning(f"Unexpected withdrawal response: {text}")
            insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)
            mark_redeem_request_status(redeem_request_id, "failed")
            update_automation_result(task_id=task_id, status="failed", description="Unexpected withdrawal response.")
    except PlaywrightTimeoutError:
        mark_redeem_request_status(redeem_request_id, "failed")
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdraw on {BACKEND_NAME}")
        insert_log("warning", "Failed to detect dialog after account withdrawal", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id)


def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    table = main.locator("table#item")
    table.wait_for(timeout=20_000, state="visible")

    row = table.locator(f"//tr[contains(@class, 'list')][td[3][normalize-space(text())='{account_id}']]").first
    row.wait_for(timeout=10_000)
    if row.is_visible():
        logger.debug("Account row located in table.")
        backend_account_id = row.locator("td:nth-child(2)").inner_text().strip()
        data = {
            "account_id": row.locator("td:nth-child(3)").inner_text().strip(),
            "nickname": row.locator("td:nth-child(4)").inner_text().strip(),
            "balance": row.locator("td:nth-child(5)").inner_text().strip(),
            "register_date": row.locator("td:nth-child(6)").inner_text().strip(),
            "last_login": row.locator("td:nth-child(7)").inner_text().strip(),
            "manager": row.locator("td:nth-child(8)").inner_text().strip(),
            "status": row.locator("td:nth-child(9)").inner_text().strip(),
        }
        update_game_id_by_username(account_id, backend_account_id)
        update_automation_result(task_id=task_id, status="success", data=json.dumps(data), description="Account information.")
        logger.info(f"Account read data: {data}")

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
            source_url=str(page.url),
            backend_id=backend.id
        )
        _login_and_navigate(page, logger, backend, task_id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger)
            page.reload(wait_until="domcontentloaded")
        update_automation_result(task_id=task_id, status="finished", description="Account creation successful.")
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
            source_url=str(page.url),
            backend_id=backend.id
        )
        update_automation_result(task_id=task_id, description=f"Error during account creation: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=backend.id)

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
            source_url=str(page.url),
            backend_id=backend.id, account_id=_.id
        )
        _login_and_navigate(page, logger, backend, task_id)
        _recharge_account(page, logger, count, account_id, order_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url),
            backend_id=backend.id, account_id=_.id
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend.id, account_id=_.id)



@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t: str, id_to_update=None):
    backend = get_backend(BACKEND_NAME)
    _ = get_backend_account(account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Freeplay-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating freeplay recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
            backend_id=backend.id, account_id=_.id
        )
        _login_and_navigate(page, logger, backend, task_id)
        _freeplay_account(page, logger, count, account_id, task_id, t, id_to_update)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url),
            backend_id=backend.id, account_id=_.id
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend.id, account_id=_.id)

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
            source_url=str(page.url),
            backend_id=backend.id,
            account_id=_.id
        )
        _login_and_navigate(page, logger, backend, task_id)
        _withdraw_account(page, logger, count, account_id, task_id, redeem_request_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url),
            backend_id=backend.id,
            account_id=_.id
        )
        update_automation_result(task_id=task_id, description=f"Error during account withdrawal.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=backend.id, account_id=_.id)

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
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url),
            backend_id=backend.id, account_id=_.id
        )
        _login_and_navigate(page, logger, backend, task_id)
        _read_account(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url),
            backend_id=backend.id,
            account_id=_.id
        )
        update_automation_result(task_id=task_id, description=f"Error during account read.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=backend.id, account_id=_.id)
