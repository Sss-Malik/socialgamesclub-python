
import json
import logging
import random

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.orionstars.config import *
from common.utils.aws_s3 import capture_and_upload_screenshot
from backends.orionstars.utils.credentials import generate_credentials
from backends.orionstars.utils.actions import click_update_for_account
from common.utils.emails import send_email

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, \
    update_order_automation_status, update_automation_result, mark_freeplay_transferred, finalize_status, \
    mark_redeem_request_status, get_backend_account, mark_bonus_transferred, update_password_by_username, \
    restore_wallet_balance, update_order_status, update_wallet_detail_status, get_backend_and_account, \
    process_recharge_operation, update_freeplay, insert_log_and_update_automation_result, process_freeplay_operation
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
        el = page.locator(MAIN_PAGE_EL)
        el.wait_for(timeout=5000, state="visible")
        logger.info("Existing session detected; skipping login.")
        try:
            alert = page.locator("div#customAlert")
            alert.wait_for(timeout=5000, state="visible")
            logger.debug("custom alert detected")
            close_btn = alert.locator("button#cancelBtn")
            close_btn.click()
        except PlaywrightTimeoutError:
            pass
        page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=20_000)
        return
    except PlaywrightTimeoutError:
        logger.info("No existing session; proceeding with login.")


    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap  = page.locator(CAPTCHA_INPUT)
    btn  = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")

        acct.fill(username)
        pwd.fill(password)

        logger.debug("Solving CAPTCHA…")
        if DEBUG:
            input("Debug mode: Solve CAPTCHA manually and press enter.")
        else:

            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
                logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                page.reload(wait_until="domcontentloaded")
                continue

            cap.fill(text)
        btn.click()

        try:
            dialog_el = page.locator("div#mb_con", has_text="incorrect")
            dialog_el.wait_for(timeout=5000, state="visible")
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
                update_automation_result(task_id=task_id, status="failed", description=f"Incorrect login for {BACKEND_NAME}.")
                raise Exception(f"Incorrect login credentials for backend: {backend.name}")
            else:
                logger.info(f"Unknown dialog message: {text}")
                break
        except PlaywrightTimeoutError:
            logger.info("Login likely successful (no error dialog detected).")

            break

    logger.info("Login successful, navigating to user management page.")

    try:
        alert = page.locator("div#customAlert")
        alert.wait_for(timeout=5000, state="visible")
        close_btn = alert.locator("button#cancelBtn")
        close_btn.click()
    except PlaywrightTimeoutError:
        pass


    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=10_000)
    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger, task_id):
    logger.debug("Opening create account dialog.")
    try:
        alert = page.locator("div#customAlert")
        alert.wait_for(timeout=2000, state="visible")
        logger.debug("custom alert detected")
        close_btn = alert.locator("button#cancelBtn")
        close_btn.click()
    except PlaywrightTimeoutError:
        pass
    page.wait_for_selector(MAIN_IFRAME, timeout=10_000)
    main_frame = page.frame_locator(MAIN_IFRAME)
    create_acc = main_frame.locator(CREATE_ACCOUNT_INIT)
    create_acc.wait_for(timeout=10_000)
    create_acc.click(timeout=10_000)

    page.wait_for_selector(CREATE_ACCOUNT_DIALOG, timeout=15_000)
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        delay = random.randint(1000, 3000)
        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        dialog.locator(ACCOUNT_ID).fill(account_id)
        dialog.locator(ACCOUNT_PASSWORD).fill(password)
        dialog.locator(CONFIRM_PASSWORD).fill(password)
        page.wait_for_timeout(delay)
        dialog.locator(CREATE_ACCOUNT).click()
        page.wait_for_timeout(delay)
        # Wait for the feedback modal
        page.locator("#mb_con").wait_for(timeout=10_000)
        msg = page.locator("#mb_msg").inner_text().strip().lower()

        if "already exists" in msg:
            logger.warning(f"Account ID already exists: {account_id}")
            page.locator("#mb_btn_ok").click()
            page.wait_for_timeout(delay)
            continue
        elif "success" in msg:
            logger.info("Account created successfully: %s", account_id)
            insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
            save_credentials(account_id, password, logger, DATA_DIR)
            page.locator("#mb_btn_ok").click()
            page.wait_for_timeout(delay)
            break
        elif "too frequent" in msg:
            logger.warning("automation detected. Aborting...")
            insert_log("warning", "Automation script detected. Aborting for now", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
            raise Exception(
                f"Automation script detected on {BACKEND_NAME} while creating accounts. Please try again later.")
        else:
            logger.warning(f"Unexpected message after creating account: {msg}")
            insert_log("warning", f"Unexpected create account response: {msg}", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
            page.locator("#mb_btn_ok").click()
            page.wait_for_timeout(delay)
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str, order_id, task_id, wallet_id, amount_to_deduct):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main_frame = page.frame_locator(MAIN_IFRAME)
    main_frame.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_frame.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(4000)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    # Click “Update” then “Recharge” via your helper
    click_update_for_account(frame, account_id, logger)
    main_frame.locator("a", has_text="Recharge").click(timeout=5000)

    # Fill the recharge amount
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()
    try:
        # Check result
        page.locator("#mb_con").wait_for(timeout=25000, state="visible")
        result = page.locator("#mb_msg").inner_text().lower()

        # Default (unexpected) outcome
        log_type = "warning"
        description = f"Unexpected recharge response: {result} on {BACKEND_NAME} - Wallet balance restored"
        order_status = "failed"
        automation_status = "failed"
        automation_result_fields = {
            "status": "failed",
            "description": description
        }
        wallet_status = "failed"
        restore_wallet = True
        amount_restore = amount_to_deduct
        wallet_to_restore = wallet_id

        bonus_transferred = False

        if "successful" in result:
            logger.info("Recharge successful.")
            log_type = "info"
            description = f"Recharge successful for account {account_id}"
            order_status = "finished"
            automation_status = "finished"
            automation_result_fields = {
                "status": "success",
                "description": description
            }
            wallet_status = "finished"
            restore_wallet = False
            amount_restore = None
            wallet_to_restore = None

            if _.user.bonus_received:
                bonus_transferred = True

        elif "insufficient" in result:
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Recharge failed: backend balance insufficient.")
            description = f"Backend balance insufficient for {BACKEND_NAME} - Wallet balance restored"
            automation_result_fields = {
                "status": "failed",
                "description": description
            }
        elif "unknown" in result:
            logger.warning("Unknown error.")
            description = f"Unknown error for recharge: {account_id} - Wallet balance restored"
            automation_result_fields = {
                "status": "failed",
                "description": description
            }
        else:  # keep default fallback
            logger.warning(f"Unexpected recharge response: {result}")

        process_recharge_operation(
            order_id=order_id,
            task_id=task_id,
            account_id=_.id,
            backend_id=BACKEND_ID,
            page_url=str(page.url),
            log_data={
                "type": log_type,
                "description": description
            },
            order_status=order_status,
            automation_status=automation_status,
            automation_result_fields=automation_result_fields,
            wallet_status=wallet_status,
            restore_wallet=restore_wallet,
            amount_to_restore=amount_restore,
            wallet_id=wallet_to_restore,
            bonus_transferred=bonus_transferred
        )
    except PlaywrightTimeoutError:
        process_recharge_operation(
            order_id=order_id,
            task_id=task_id,
            account_id=_.id,
            backend_id=BACKEND_ID,
            page_url=str(page.url),
            log_data={
                "type": "warning",
                "description": f"Failed to detect dialog after recharge for account: {account_id} - Wallet balance restored"
            },
            order_status="failed",
            automation_status="failed",
            automation_result_fields={
                "status": "failed",
                "description": f"Failed to detect result after recharge on {BACKEND_NAME}"
            },
            wallet_status="failed",
            restore_wallet=True,
            amount_to_restore=amount_to_deduct,
            wallet_id=wallet_id,
        )
        logger.info("Wallet balance restored")

def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update, freeplay_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main_frame = page.frame_locator(MAIN_IFRAME)
    main_frame.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_frame.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(4000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()

    # Click “Update” then “Recharge” via your helper
    click_update_for_account(frame, account_id, logger)
    main_frame.locator("a", has_text="Recharge").click(timeout=5000)

    # Fill the recharge amount
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()
    try:
        # Check result
        page.locator("#mb_con").wait_for(timeout=25000, state="visible")
        result = page.locator("#mb_msg").inner_text().lower()

        # Default values
        log_type = "warning"
        description = f"Unexpected recharge response: {result}"
        result_status = "failed"

        if "successful" in result:
            logger.info("Recharge successful.")
            log_type = "info"
            description = f"Freeplay Recharge successful for account: {account_id}"
            result_status = "success"
            process_freeplay_operation(
                t=t,
                username=account_id,
                account_id=_.id,
                backend_id=BACKEND_ID,
                task_id=task_id,
                freeplay_id=freeplay_id,
                id_to_update=id_to_update,
            )
        elif "insufficient" in result:
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            description = f"Insufficient backend balance for {BACKEND_NAME}"

        elif "unknown" in result:
            logger.warning("Unknown error.")
            description = f"Unknown error for recharge: {account_id} - Wallet balance restored"
        else:
            logger.warning(f"Unexpected recharge response: {result}")

        insert_log_and_update_automation_result(
            log_type=log_type,
            log_description=description,
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status=result_status,
            result_description=description,
        )
    except PlaywrightTimeoutError:
        insert_log_and_update_automation_result(
            log_type="warning",
            log_description=f"Failed to detect dialog after recharge for account: {account_id}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status="failed",
            result_description=f"Failed to detect result after recharge on {BACKEND_NAME}",
        )


def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(4000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    row = click_update_for_account(frame, account_id, logger)
    table = main.locator("table#item")
    table.wait_for(timeout=5000, state="visible")

    row = table.locator(
        f"//tr[contains(@class, 'list')][td[3][normalize-space(text())='{account_id}']]"
    ).first
    row.wait_for(timeout=5000)
    if row.is_visible():
        logger.debug("Account row located in table.")
        update_btn = row.locator("td:nth-child(1) a")
        update_btn.click()
        page.wait_for_timeout(2000)
        backend_account_id = row.locator("td:nth-child(2)").inner_text().strip()
        data = {
            "account_id": row.locator("td:nth-child(3)").inner_text().strip(),
            "nickname": row.locator("td:nth-child(4)").inner_text().strip(),
            "balance": main.locator("#txtBalance").inner_text().strip(),
            "register_date": row.locator("td:nth-child(5)").inner_text().strip(),
            "last_login": row.locator("td:nth-child(6)").inner_text().strip(),
            "manager": row.locator("td:nth-child(7)").inner_text().strip(),
            "status": row.locator("td:nth-child(8)").inner_text().strip(),
        }
        update_game_id_by_username(account_id, backend_account_id)
        update_automation_result(task_id=task_id, status="success", description="Account information.", data=json.dumps(data))
        logger.info(f"Account read data: {data}")


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id, order_id, requested_amount):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5_000)  # wait for results to show up

    # click the “Update” (via your helper) then Recharge
    # note: click_update_for_account still expects a Frame object, so we grab it here:
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Redeem").click()

    # fill recharge count
    redeem = page.frame_locator('iframe[src*="AccountManager"]')
    customer_balance = redeem.locator('input#txtLeScore').get_attribute('value')
    logger.debug(f"Extracted value: {customer_balance}")

    if count > float(customer_balance):
        logger.error("Withdraw failed: insufficient customer balance.")
        insert_log_and_update_automation_result(
            log_type="warning",
            log_description="Insufficient customer balance",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status="failed",
            result_description="Insufficient customer balance.",
            redeem_request_id=redeem_request_id,
            redeem_request_status="failed",
            order_id=order_id,
            wallet_detail_status="failed",
            add_to_wallet = False
        )
        return

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated. Press Enter to continue...")

    redeem.locator('input[type="button"][value="Redeem"]').click()

    try:
        # feedback
        page.locator("#mb_con").wait_for(timeout=25000)
        text = page.locator("#mb_con").inner_text().lower().strip()

        # Default values
        log_type = "warning"
        description = f"Unexpected withdrawal response: {text}"
        result_status = "failed"
        redeem_request_status = "failed"

        wallet_detail_status = "failed"
        add_to_wallet = False
        add_to_wallet_amount = requested_amount

        if "successful" in text:
            logger.info("Withdraw successful.")
            log_type = "info"
            description = f"Withdrawal successful for account: {account_id}"
            result_status = "success"
            redeem_request_status = "processed"
            wallet_detail_status = "finished",
            add_to_wallet = True,

        elif "not enough gold" in text:
            logger.error("Withdrawal failed due to insufficient gold.")
            description = "Insufficient customer balance."

        else:
            logger.warning(f"Unexpected withdrawal response: {text}")

        insert_log_and_update_automation_result(
            log_type=log_type,
            log_description=description,
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status=result_status,
            result_description=description,
            redeem_request_id=redeem_request_id,
            redeem_request_status=redeem_request_status,
            order_id=order_id,
            wallet_detail_status=wallet_detail_status,
            add_to_wallet=add_to_wallet,
            add_to_wallet_amount=add_to_wallet_amount
        )
    except PlaywrightTimeoutError:
        insert_log_and_update_automation_result(
            log_type="warning",
            log_description="Failed to detect dialog after account withdrawal",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status="failed",
            result_description=f"Failed to detect result after withdraw on {BACKEND_NAME}",
            redeem_request_id=redeem_request_id,
            redeem_request_status="failed",
            order_id=order_id,
            wallet_detail_status="failed",
            add_to_wallet=False
        )

def _reset_password(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Initiating password reset for account {account_id}")
    _ = get_backend_account(account_id)
    __, password = generate_credentials()

    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Reset Password").click()
    reset = page.frame_locator('iframe[src*="AccountManager"]')

    reset.locator("input#txtConfirmPass").fill(password)
    reset.locator("input#txtSureConfirmPass").fill(password)

    reset.locator('input[type="button"][value="Reset"]').click()

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()

        # Default values
        log_type = "warning"
        description = f"Password reset failed. Unhandled reset response: {text}"
        result_data: dict | None = None
        result_status = "failed"

        if "modified success" in text:
            logger.info("Password reset successful.")
            log_type = "info"
            description = f"Password reset successful for account {account_id}"
            result_data = {"password": password}
            result_status = "success"
            update_password_by_username(username=account_id, new_password=password)
        else:
            logger.warning(f"Password reset failed. Unhandled reset response: {text}")

        insert_log_and_update_automation_result(
            log_type=log_type,
            log_description=description,
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status=result_status,
            result_description=description,
            result_data=result_data,
        )
    except PlaywrightTimeoutError:
        logger.warning("Password reset failed. Failed to detect result after reset")
        insert_log_and_update_automation_result(
            log_type="error",
            log_description="Failed to detect reset response",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=BACKEND_ID,
            account_id=_.id,
            result_status="failed",
            result_description="Failed to detect reset response",
        )

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
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        _login_and_navigate(page, logger, backend, task_id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger, task_id)
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
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account creation: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend.id,
            result_status="failed",
            result_description=f"Error during account creation: {e}",
            screenshot_url=screenshot_url
        )

    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend, wallet_id, amount_to_deduct):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, task_id=task_id, account_id=backend_account.id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _recharge_account(page, logger, count, account_id, order_id, task_id, wallet_id, amount_to_deduct)
    except (PlaywrightTimeoutError, Exception) as e:
        restore_wallet_balance(wallet_id, amount_to_deduct)
        insert_log("info", "Critical error during account recharge - Wallet balance restored", source_url=str(page.url),
                   backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend_game.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during account recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"<WALLET_RESTORED> - Error during account recharge: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"<WALLET_RESTORED> - Error during account recharge: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )

    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t, id_to_update, freeplay_id):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, task_id=task_id, account_id=backend_account.id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _freeplay_account(page, logger, count, account_id, task_id, t, id_to_update, freeplay_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend_game.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        send_email(
            subject="Account recharge failed",
            body=f"Critical error occurred during freeplay recharge for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account freeplay recharge: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account freeplay recharge: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )

    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id, order_id, requested_amount):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _withdraw_account(page, logger, count, account_id, task_id, redeem_request_id, order_id, requested_amount)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend_game.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        send_email(
            subject="Account withdrawal failed",
            body=f"Critical error occurred during account withdrawal for account ID {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account withdrawal: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account withdrawal: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=BACKEND_ID, account_id=backend_account.id, task_id=task_id)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=backend_game.id, task_id=task_id, account_id=backend_account.id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _read_account(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend_game.name,
            task_id=task_id,
            account_id=account_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account read: %s", e, exc_info=True)
        send_email(
            subject="Account read failed",
            body=f"Critical error occurred during reading account {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account read: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account read: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_reset_password(page: Page, account_id: str, task_id, backend):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Reset-password action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating password reset for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _reset_password(page, logger, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        screenshot_url = capture_and_upload_screenshot(
            page=page,
            backend=backend_game.name,
            account_id=account_id,
            task_id=task_id,
        )
        logger.error("Screenshot captured and uploaded: %s", screenshot_url)
        logger.critical("Error during account password reset: %s", e, exc_info=True)
        send_email(
            subject="Account password reset failed",
            body=f"Critical error occurred during reset password for {account_id} on backend '{BACKEND_NAME}'. Please review",
        )
        insert_log_and_update_automation_result(
            log_type="error",
            log_description=f"Error during account password reset: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"Error during account password reset: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )
    finally:
        logger.info("Reset-password action completed.")
        insert_log("info", "Reset password action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)