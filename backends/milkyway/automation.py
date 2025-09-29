import random
import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.milkyway.config import *
from backends.milkyway.utils.credentials import generate_credentials
from backends.milkyway.utils.actions import click_update_for_account
from common.utils.aws_s3 import capture_and_upload_screenshot
from common.utils.emails import send_email

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, \
    update_order_automation_status, update_automation_result, mark_freeplay_transferred, finalize_status, \
    mark_redeem_request_status, get_backend_account, mark_bonus_transferred, update_password_by_username, \
    restore_wallet_balance, update_order_status, update_wallet_detail_status, get_backend_and_account, \
    process_recharge_operation
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
        page.locator(MAIN_PAGE_EL).wait_for(timeout=5000)
        logger.info("Existing session detected; skipping login.")
        page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=20_000)
        return
    except PlaywrightTimeoutError:
        try:
            dialog_el = page.locator("div#mb_con")
            dialog_el.wait_for(timeout=5000, state="visible")
            text = dialog_el.inner_text().strip().lower()
            if "session timeout" in text:
                logger.info("Session timeout dialog detected. resolving")
                page.locator("input#mb_btn_ok").click()
                page.wait_for_load_state("networkidle")
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

        logger.debug("Solving CAPTCHA")
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
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=10_000)
    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger, task_id):
    logger.debug("Opening create account dialog.")

    page.wait_for_selector(MAIN_IFRAME, timeout=10_000)
    main_frame = page.frame_locator(MAIN_IFRAME)
    create_acc = main_frame.locator(CREATE_ACCOUNT_INIT)
    create_acc.wait_for(timeout=10_000)
    create_acc.click(timeout=10_000)

    page.wait_for_selector(CREATE_ACCOUNT_DIALOG, timeout=15_000)
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        delay = random.randint(1000, 10000)
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
            save_credentials(account_id, password, logger, DATA_DIR)
            insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
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

    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)
    main_frame.locator("a", has_text="Recharge").click(timeout=5000)

    # Fill the recharge amount
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()

    # Check result

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()

        if "successful" in text:
            logger.info("Recharge successful.")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=_.id,
                backend_id=BACKEND_ID,
                page_url=str(page.url),
                log_data={
                    "type": "info",
                    "description": f"Recharge successful for account: {account_id}"
                },
                order_status="finished",
                automation_status="finished",
                automation_result_fields={
                    "status": "success",
                    "description": "Recharge successful"
                },
                wallet_status="finished"
            )
            if _.user.bonus_received:
                mark_bonus_transferred(account_id)

        elif "insufficient" in text:
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Recharge failed: backend balance insufficient.")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=_.id,
                backend_id=BACKEND_ID,
                page_url=str(page.url),
                log_data={
                    "type": "warning",
                    "description": "Backend balance insufficient - Wallet balance restored"
                },
                order_status="failed",
                automation_status="failed",
                automation_result_fields={
                    "status": "failed",
                    "description": f"Insufficient backend balance for {BACKEND_NAME}"
                },
                wallet_status="failed",
                restore_wallet=True,
                amount_to_restore=amount_to_deduct,
                wallet_id=wallet_id,
            )
            logger.info("Wallet balance restored")
            return
        elif "unknown" in text:
            logger.warning("Unknown error.")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=_.id,
                backend_id=BACKEND_ID,
                page_url=str(page.url),
                log_data={
                    "type": "warning",
                    "description": f"Unknown error for recharge: {account_id} - Wallet balance restored"
                },
                order_status="failed",
                automation_status="failed",
                automation_result_fields={
                    "status": "failed",
                    "description": "Unknown error."
                },
                wallet_status="failed",
                restore_wallet=True,
                amount_to_restore=amount_to_deduct,
                wallet_id=wallet_id,
            )
            logger.info("Wallet balance restored")
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            process_recharge_operation(
                order_id=order_id,
                task_id=task_id,
                account_id=_.id,
                backend_id=BACKEND_ID,
                page_url=str(page.url),
                log_data={
                    "type": "warning",
                    "description": f"Unexpected recharge response: {text} - Wallet balance restored"
                },
                order_status="failed",
                automation_status="failed",
                automation_result_fields={
                    "status": "failed",
                    "description": f"Unexpected recharge response on {BACKEND_NAME}"
                },
                wallet_status="failed",
                restore_wallet=True,
                amount_to_restore=amount_to_deduct,
                wallet_id=wallet_id,
            )
            logger.info("Wallet balance restored")
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


def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main_frame = page.frame_locator(MAIN_IFRAME)
    main_frame.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_frame.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(4000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()

    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)
    main_frame.locator("a", has_text="Recharge").click(timeout=5000)

    # Fill the recharge amount
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator('input[type="button"][value="Recharge"]').click()

    # Check result

    try:
        result = page.locator("#mb_con")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()

        if "successful" in text:
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
            if t == "signup_freeplay":
                mark_freeplay_transferred(account_id)
            else:
                finalize_status(t, True, id_to_update)
        elif "insufficient" in text:
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            insert_log("warning", "Backend balance insufficient", source_url=str(page.url),
                       backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
            return
        elif "unknown" in text:
            update_automation_result(task_id=task_id, status="failed", description="Unknown error.")
            insert_log("warning", f"Unknown error for recharge: {account_id} ", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
            insert_log("warning", f"Unexpected recharge response: {result}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")
        insert_log("warning", f"Failed to detect result after recharge on {BACKEND_NAME}", source_url=str(page.url),
                   backend_id=BACKEND_ID, account_id=_.id)

def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5000)

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    logger.debug("Calling click_update_for_account helper.")
    click_update_for_account(frame, account_id, logger)

    table = main.locator("table#item")
    table.wait_for(timeout=5000, state="visible")

    row = table.locator(
        f"//tr[contains(@class, 'list')][td[3][normalize-space(text())='{account_id}']]"
    ).first
    row.wait_for(timeout=5000)
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


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id):
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
    logger.info(f"Extracted value: {customer_balance}")

    if count > float(customer_balance):
        logger.error("Withdraw failed: insufficient customer balance.")
        update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
        insert_log("info", "Insufficient customer balance", source_url=str(page.url),
                   backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        mark_redeem_request_status(redeem_request_id, "failed")
        return

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated. Press Enter to continue...")

    redeem.locator('input[type="button"][value="Redeem"]').click()

    # feedback
    try:
        page.locator("#mb_con").wait_for(timeout=25000)
        text = page.locator("#mb_con").inner_text().lower().strip()
        if "successful" in text:
            logger.info("Withdraw successful.")
            update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
            mark_redeem_request_status(redeem_request_id, "processed")
            insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        elif "not enough gold" in text:
            logger.error("Withdrawal failed due to insufficient gold.")
            update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
            insert_log("info", "Insufficient customer balance", source_url=str(page.url),
                       backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            mark_redeem_request_status(redeem_request_id, "failed")
            return
        else:
            logger.warning(f"Unexpected withdrawal response: {text}")
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
            mark_redeem_request_status(redeem_request_id, "failed")
            insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
    except PlaywrightTimeoutError:
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdraw on {BACKEND_NAME}")
        mark_redeem_request_status(redeem_request_id, "failed")
        insert_log("info", f"Failed to detect result after withdraw on {BACKEND_NAME}", source_url=str(page.url),
                   backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)



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

        if "modified success" in text:
            logger.info("Password reset successful.")
            insert_log("info", description=f"Password reset successful for account {account_id}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            update_automation_result(task_id=task_id, description="Password reset successful.", status="success", data=json.dumps({"password": password}))
            update_password_by_username(username=account_id, new_password=password)
        else:
            logger.warning(f"Password reset failed. Unhandled reset response: {text}")
            insert_log("error", description=f"Password reset failed. Unhandled reset response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
            update_automation_result(task_id=task_id, description=f"Password reset failed. Unhandled reset response: {text}", status="failed")
    except PlaywrightTimeoutError:
        logger.warning("Password reset failed. Failed to detect result after reset")
        insert_log("error", description="Failed to detect reset response", source_url=str(page.url), backend_id=BACKEND_ID, account_id=_.id, task_id=task_id)
        update_automation_result(task_id=task_id, description="Failed to detect reset response", status="failed")



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
        insert_log(
            "error",
            f"Error during account creation: {e}",
            source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Account creation failed. {e}", status="failed", screenshot_url=screenshot_url)
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
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
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
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t, id_to_update):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _freeplay_account(page, logger, count, account_id, task_id, t, id_to_update)
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
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account recharge failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        _login_and_navigate(page, logger, backend_game, task_id)
        _withdraw_account(page, logger, count, account_id, task_id, redeem_request_id)
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
        insert_log(
            "error",
            f"Error during account withdrawal: {e}",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, status="failed", description=f"Account withdrawal failed. {e}", screenshot_url=screenshot_url)
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
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
        insert_log(
            "error",
            f"Error during account read: {e}",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Account read failed. {e}", status="failed", screenshot_url=screenshot_url)
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
        insert_log(
            "error",
            f"Error during account password reset: {e}",
            source_url=str(page.url),
            backend_id=backend_game.id,
            account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account password reset.{e}", status="failed", screenshot_url=screenshot_url)
    finally:
        logger.info("Reset-password action completed.")
        insert_log("info", "Reset password action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)