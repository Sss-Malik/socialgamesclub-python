import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.gameroom.config import *
from backends.gameroom.utils.credentials import generate_credentials
from backends.gameroom.utils.actions import click_account_action
from common.utils.aws_s3 import capture_and_upload_screenshot
from common.utils.emails import send_email
import random
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.handle_captcha import handle_captcha
from common.utils.redis_utils import acquire_login_lock, release_login_lock
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, \
    update_order_automation_status, update_automation_result, mark_freeplay_transferred, invalidate_latest_session, \
    create_backend_session, increment_active_tasks_count, decrement_active_tasks_count, finalize_status, \
    mark_redeem_request_status, get_backend_account, mark_bonus_transferred, update_password_by_username, \
    restore_wallet_balance, update_order_status, update_wallet_detail_status, get_backend_and_account, \
    process_recharge_operation, update_freeplay, insert_log_and_update_automation_result, process_freeplay_operation

from common.utils.browser import with_persistent_browser
from common.utils.poll_utils import wait_for_valid_session, wait_for_active_tasks_to_zero
from backends.gameroom.utils.session import inject_session_token, validate_session_token

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):

    page.goto(backend.backend_url, wait_until="domcontentloaded")

    session = wait_for_valid_session(backend.name, logger)

    if session:
        logger.info("Valid session found, attempting to inject...")
        inject_session_token(page, session.token, session.expires, backend.backend_url)

        if validate_session_token(page, logger):
            logger.info("Session injection and validation successful")

            game_user = page.locator('a', has_text="Game User")
            game_user.wait_for(state="visible", timeout=20_000)
            game_user.click()

            user_mgmt = page.locator(USER_MANAGEMENT_EL)
            user_mgmt.wait_for(state="visible", timeout=20_000)
            user_mgmt.click()
            page.wait_for_timeout(3000)
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

            logger.info("Initiating login process.")
            logger.debug("Fetching backend details from db...")

            username = backend.username or USERNAME
            password = backend.password or PASSWORD

            logger.debug(f"Using credentials -> username: {username}, login_url: {backend.backend_url}")

            logger.debug("Navigating to login page at: %s", LOGIN_URL)

            acct = page.locator(LOGIN_ACCOUNT)
            pwd = page.locator(LOGIN_PASSWORD)
            cap_in = page.locator(CAPTCHA_INPUT)
            btn = page.locator(LOGIN_BUTTON)

            for attempt in range(MAX_CAPTCHA_RETRIES):
                logger.debug(f"Login attempt #{attempt + 1}")
                acct.fill(username)
                pwd.fill(password)

                logger.debug("Solving CAPTCHA…")
                if DEBUG:
                    input("Debug mode: Solve CAPTCHA manually and press enter.")
                else:
                    page.wait_for_timeout(2000)
                    text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
                    if not text or text == 0:
                        logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                        page.reload(wait_until="domcontentloaded")
                        continue

                    cap_in.fill(text)
                btn.click()
                try:
                    dialog_el = page.locator("div.layui-layer.layui-layer-dialog")
                    dialog_el.wait_for(timeout=5000, state="visible")
                    text = dialog_el.inner_text().strip().lower()
                    if "the verification code is incorrect" in text:
                        logger.warning("Incorrect CAPTCHA entered.")
                        if not DEBUG:
                            solver.report_incorrect_image_captcha()
                            page.reload(wait_until="domcontentloaded")
                    elif "username or password error" in text:
                        logger.error("Incorrect login credentials.")
                        update_automation_result(task_id=task_id, status="failed",
                                                     description=f"Incorrect login for {BACKEND_NAME}")
                        raise Exception(f"Incorrect login credentials for backend: {backend.name}")
                    else:
                        logger.info(f"Unknown dialog message: {text}")
                        break
                except PlaywrightTimeoutError:
                    logger.info("Login likely successful (no error dialog detected).")
                    break

            logger.info("Login successful, navigating to user management page.")
            page.locator(MAIN_PAGE_EL).wait_for(state="attached", timeout=60_000)

            admin_token = page.evaluate("() => sessionStorage.getItem('token')")
            expires_time = page.evaluate("() => sessionStorage.getItem('expires_time')")
            new_session = create_backend_session(backend.name, token=admin_token, expires=expires_time)


            game_user = page.locator('a', has_text="Game User")
            game_user.wait_for(state="visible", timeout=20_000)
            game_user.click()
            user_mgmt = page.locator(USER_MANAGEMENT_EL)
            user_mgmt.wait_for(state="visible", timeout=20_000)
            user_mgmt.click()
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

        inject_session_token(page, session.token, session.expires, backend.backend_url)
        if not validate_session_token(page, logger):
            update_automation_result(task_id=task_id, status="failed", description="Session after wait was invalid")
            raise Exception("Session after wait was invalid")



        game_user = page.locator('a', has_text="Game User")
        game_user.wait_for(state="visible", timeout=20_000)
        game_user.click()

        user_mgmt = page.locator(USER_MANAGEMENT_EL)
        user_mgmt.wait_for(state="visible", timeout=20_000)
        user_mgmt.click()
        logger.info("Login and navigation successful.")


        logger.info("Session from another task injected and validated.")
        return session

def _create_single_account(page: Page, logger: logging.Logger, task_id):
    logger.debug("Opening create account dialog.")
    main_iframe = page.frame_locator(MAIN_IFRAME)
    main_iframe.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    dialog_iframe = main_iframe.frame_locator(DIALOG_IFRAME)
    dialog_iframe.locator(ACCOUNT_ID).wait_for(timeout=10_000)

    while True:
        delay = random.randint(1000, 3000)
        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        dialog_iframe.locator(ACCOUNT_ID).fill(account_id)
        dialog_iframe.locator(ACCOUNT_BALANCE).fill("0")
        dialog_iframe.locator(ACCOUNT_PASSWORD).fill(password)
        dialog_iframe.locator(CONFIRM_PASSWORD).fill(password)
        page.wait_for_timeout(delay)
        dialog_iframe.locator(CREATE_ACCOUNT).click()

        try:
            # wait for the post‐submit message
            msg = dialog_iframe.locator(ACCOUNT_SUCCESS)
            msg.wait_for(state="visible", timeout=10_000)
            text = msg.inner_text().strip().lower()
            
            if "username already exists" in text:
                logger.warning(f"Account ID already exists: {account_id}")
                page.wait_for_timeout(delay)
                continue
            elif "successful" in text:
                logger.info("Account created successfully: %s", account_id)
                insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                save_credentials(account_id, password, logger, DATA_DIR)
                page.wait_for_timeout(1_000)
                main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
                page.wait_for_timeout(delay)
                break
            else:
                logger.warning(f"Unexpected message after creating account: {text}")
                insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
                page.wait_for_timeout(delay)
                break

        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)
            page.wait_for_timeout(delay)
            break


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, redeem_request_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main_iframe = page.frame_locator(MAIN_IFRAME)

    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    logger.debug("Calling click_withdraw_for_account helper.")
    click_account_action(frame_obj, account_id, "withdraw", logger)

    page.wait_for_timeout(1000)

    # fill & submit recharge form
    withdraw_iframe = main_iframe.frame_locator('iframe[src*="withdraw"]')

    withdraw_iframe.locator("div.layui-form-item:has(label:text('Withdraw Balance')) input").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue withdrawal.")

    withdraw_iframe.locator("button:has-text('Submit')").click()
    # wait for confirmation
    try:
        result = withdraw_iframe.locator("div.layui-layer.layui-layer-dialog")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Withdraw successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Withdrawal successful for account: {account_id}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="success",
                result_description="Withdraw successful.",
                redeem_request_id=redeem_request_id,
                redeem_request_status="processed"
            )
        elif "withdrawal amount is greater than customer balance" in text:
            logger.error("Withdrawal failed due to insufficient gold.")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Insufficient customer balance.",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="failed",
                result_description="Insufficient customer balance.",
                redeem_request_id=redeem_request_id,
                redeem_request_status="failed"
            )
            return
        else:
            logger.warning(f"Unexpected withdrawal response: {text}")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description=f"Unexpected withdrawal response: {text}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="failed",
                result_description="Unexpected withdrawal response.",
                redeem_request_id=redeem_request_id,
                redeem_request_status="failed"
            )
    except PlaywrightTimeoutError:
        logger.error("Failed to detect result dialog after account withdrawal.")
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
            redeem_request_status="failed"
        )


def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    row = click_account_action(frame_obj, account_id, "read", logger)

    logger.debug("Account row located in table.")
    backend_account_id = row.locator("td[data-field='Id']").inner_text().strip()
    data = {
        "id": row.locator("td[data-field='Id']").inner_text().strip(),
        "account": row.locator("td[data-field='Account']").inner_text().strip(),
        "nickname": row.locator("td[data-field='nickname']").inner_text().strip(),
        "balance": row.locator("td[data-field='score']").inner_text().strip(),
        "created_at": row.locator("td[data-field='AddDate']").inner_text().strip(),
        "login_count": row.locator("td[data-field='LoginCount']").inner_text().strip(),
        "last_login": row.locator("td[data-field='lasttime']").inner_text().strip(),
        "last_login_ip": row.locator("td[data-field='loginip']").inner_text().strip(),
    }
    update_game_id_by_username(account_id, backend_account_id)
    update_automation_result(task_id=task_id, description="Account information", data=json.dumps(data), status="success")
    logger.info(f"Account read data: {data}")


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str, order_id, task_id, wallet_id, amount_to_deduct):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)

    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    click_account_action(frame_obj, account_id, "recharge", logger)

    # fill & submit recharge form
    recharge_iframe = main_iframe.frame_locator('iframe[src*="recharge"]')
    recharge_iframe.locator('input[name="balance"]').fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge_iframe.locator("button:has-text('Submit')").click()

    # wait for confirmation
    try:
        result = recharge_iframe.locator(ACCOUNT_RECHARGE_SUCCESS)
        result.wait_for(timeout=25000)
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
            main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()

            if _.user.bonus_received:
                mark_bonus_transferred(account_id)


        elif "recharge balance is greater than available balance" in text:
            logger.error("Recharge failed: backend balance insufficient.")
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
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
                wallet_id=wallet_id
            )
            logger.info("Wallet balance restored")
            return
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
                wallet_id=wallet_id
            )
            logger.info("Wallet balance restored")
    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
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
            wallet_id=wallet_id
        )
        logger.info("Wallet balance restored")


def _freeplay_account(page: Page, logger: logging.Logger, count: int, account_id: str, task_id, t, id_to_update, freeplay_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    _ = get_backend_account(account_id)
    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    click_account_action(frame_obj, account_id, "recharge", logger)

    # fill & submit recharge form
    recharge_iframe = main_iframe.frame_locator('iframe[src*="recharge"]')
    recharge_iframe.locator('input[name="balance"]').fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge_iframe.locator("button:has-text('Submit')").click()

    # wait for confirmation
    try:
        result = recharge_iframe.locator(ACCOUNT_RECHARGE_SUCCESS)
        result.wait_for(timeout=25000)
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Recharge successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Freeplay Recharge successful for account: {account_id}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="success",
                result_description="Freeplay Recharge successful",
            )
            process_freeplay_operation(
                t=t,
                username=account_id,
                account_id=_.id,
                freeplay_id=freeplay_id,
                id_to_update=id_to_update,
                backend_id=BACKEND_ID,
                task_id=task_id,
            )
            main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
        elif "recharge balance is greater than available balance" in text:
            send_email(
                subject="Recharge failed",
                body=f"Recharge failed for account: {account_id} because of insufficient balance on {BACKEND_NAME}.",
            )
            logger.error("Recharge failed: backend balance insufficient.")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Backend balance insufficient",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="failed",
                result_description=f"Insufficient backend balance for {BACKEND_NAME}",
            )
            return
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description=f"Unexpected recharge response: {text}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="failed",
                result_description=f"Unexpected recharge response on {BACKEND_NAME}",
            )
    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
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


def _reset_password(page: Page, logger: logging.Logger, account_id: str, task_id: str):
    logger.info(f"Initiating Reset password: account_id={account_id}")
    _ = get_backend_account(account_id)
    __, password = generate_credentials()

    main_iframe = page.frame_locator(MAIN_IFRAME)

    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    logger.debug("Calling click_reset_password_for_account helper.")
    click_account_action(frame_obj, account_id, "reset_password", logger)

    reset_iframe = main_iframe.frame_locator('iframe[src*="resetpw"]')
    reset_iframe.get_by_placeholder("Please enter Login password").fill(password)
    reset_iframe.get_by_placeholder("Please enter Confirm password").fill(password)
    reset_iframe.locator("button:has-text('Submit')").click()


    try:
        result = reset_iframe.locator("div.layui-layer.layui-layer-dialog")
        result.wait_for(timeout=25000, state="visible")
        text = result.inner_text().strip().lower()

        if "reset successful" in text:
            logger.info("Password reset successful.")
            insert_log_and_update_automation_result(
                log_type="info",
                log_description=f"Password reset successful for account {account_id}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="success",
                result_description="Password reset successful.",
                result_data={"password": password}
            )
            update_password_by_username(username=account_id, new_password=password)
        else:
            logger.warning(f"Password reset failed. Unhandled reset response: {text}")
            insert_log_and_update_automation_result(
                log_type="error",
                log_description=f"Password reset failed. Unhandled reset response: {text}",
                task_id=task_id,
                source_url=str(page.url),
                backend_id=BACKEND_ID,
                account_id=_.id,
                result_status="failed",
                result_description=f"Password reset failed. Unhandled reset response: {text}",
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

    session = None

    try:
        insert_log(
            "info",
            f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
            backend_id=BACKEND_ID, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend, task_id)
        if session:
            increment_active_tasks_count(session.id)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger, task_id)
            page.reload(wait_until="domcontentloaded")

            game_user = page.locator('a', has_text="Game User")
            game_user.wait_for(state="visible", timeout=20_000)
            game_user.click()

            user_mgmt = page.locator(USER_MANAGEMENT_EL)
            user_mgmt.wait_for(state="visible", timeout=20_000)
            user_mgmt.click()

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
            source_url=str(page.url),
            backend_id=backend.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account creation. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=backend.id, task_id=task_id)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend, wallet_id, amount_to_deduct):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)



@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t: str, id_to_update, freeplay_id):
    backend_game, backend_account = get_backend_and_account(backend, account_id)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge. {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)

@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
            source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account withdrawal: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id)
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
            source_url=str(page.url),
            backend_id=backend_game.id, task_id=task_id
        )
        update_automation_result(task_id=task_id, description=f"Error during account read: {e}", status="failed", screenshot_url=screenshot_url)
    finally:
        if session:
            decrement_active_tasks_count(session.id)
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=backend_game.id, task_id=task_id)



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
