# automation_ultrapanda.py
import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.ultrapanda.config import *
from backends.ultrapanda.utils.credentials import generate_credentials
from backends.ultrapanda.utils.actions import click_set_score, click_edit
from common.utils.aws_s3 import capture_and_upload_screenshot
from common.utils.emails import send_email
import random
from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.otp import generate_2fa_code
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_order_automation_status, \
    update_automation_result, mark_freeplay_transferred, create_backend_session, invalidate_latest_session, \
    increment_active_tasks_count, decrement_active_tasks_count, finalize_status, mark_redeem_request_status, \
    get_backend_account, mark_bonus_transferred, update_password_by_username, restore_wallet_balance, \
    update_order_status, update_wallet_detail_status, get_backend_and_account, process_recharge_operation, \
    update_freeplay, insert_log_and_update_automation_result, process_freeplay_operation
from common.utils.browser import with_persistent_browser
from common.utils.poll_utils import wait_for_valid_session, wait_for_active_tasks_to_zero
from backends.ultrapanda.utils.session import inject_session_token, validate_session_token
from settings import APP_ENV, HEADLESS, DEBUG
from common.utils.redis_utils import acquire_login_lock, release_login_lock

def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):

    page.goto(backend.backend_url, wait_until="domcontentloaded")

    session = wait_for_valid_session(backend.name, logger)
    if session:
        logger.info("Valid session found, attempting to inject...")
        inject_session_token(page, session.token)
        if validate_session_token(page, logger):
            logger.info("Session injection and validation successful")
            page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)

            page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")

            try:
                # Element-UI message box is NOT a role="dialog", so rely on structure + text
                notification = page.locator(
                    "div.el-message-box"
                ).filter(
                    has_text="You have slightly low balance"
                )

                # Wait briefly — this dialog is usually fast if it appears
                notification.wait_for(state="visible", timeout=2000)

                # Click the OK button *within this message box only*
                notification.locator("button", has_text="OK").click()

                logger.info("Low balance notification detected and dismissed")

            except PlaywrightTimeoutError:
                # Notification did not appear — safe to continue
                logger.info("Low balance notification not present, continuing.")

            try:
                password_change_dialog = page.get_by_role("dialog", name="Hint").filter(
                    has_text="your account password has not been changed for a long time"
                )
                password_change_dialog.wait_for(state="visible", timeout=2000)
                logger.info("Password change reminder dialog detected")

                # Example action: click "Modify next time"
                password_change_dialog.get_by_role(
                    "button", name="Modify next time"
                ).click()

            except PlaywrightTimeoutError:
                logger.info("Password change reminder dialog not present")

            try:
                # 1. Define the dialog by Role + Name AND filter by the specific text content
                # This resolves the strict mode violation by ignoring the other "Hint" dialog.
                dialog = page.get_by_role("dialog", name="Hint").filter(
                    has_text="To ensure the security of your account"
                )

                # 2. Wait for this specific dialog to be visible
                dialog.wait_for(state="visible", timeout=3000)

                # 3. Click the Confirm button inside this specific dialog
                dialog.get_by_role("button", name="confirm").click()

                logger.info("google authenticator bind dialog detected and closed")

            except PlaywrightTimeoutError:
                # Dialog did not appear — safe to continue
                logger.info("google authenticator bind dialog not present, continuing.")
            return session
        else:
            logger.warning("Session injection failed. Invalidating session.")
            if wait_for_active_tasks_to_zero(session.id, page, logger=logger):
                logger.info("Session is now free, invalidating it.")
                invalidate_latest_session(backend.name)
            else:
                update_automation_result(task_id=task_id, status="failed",
                                         description="Session still in use. Aborting to avoid conflicts")
                raise Exception("Session still in use after waiting. Aborting to avoid conflicts")

    page.wait_for_timeout(4000)
    logger.info("No valid session. Attempting to acquire login lock.")
    if acquire_login_lock(backend.name):
        try:
            logger.info("Lock acquired. Proceeding with login.")

            page.goto(backend.backend_url, wait_until="domcontentloaded")

            try:
                page.wait_for_selector('div[role="dialog"][aria-label="announcement"]', timeout=5000)
                page.locator('div[role="dialog"][aria-label="announcement"] button:has-text("confirm")').click()
            except PlaywrightTimeoutError:
                pass

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
                auth_dialog = page.locator("div[role='dialog']", has_text="Verify your identity")
                auth_dialog.wait_for(state="visible", timeout=5000)
                logger.info("2FA detected. Solving")

                input_box = auth_dialog.locator("input[placeholder='Verification code']")
                code = generate_2fa_code(secret_key=backend.binding_key)

                input_box.fill(code)
                page.wait_for_timeout(500)  # small delay

                ok_button = auth_dialog.locator("button:has-text('OK')")
                ok_button.wait_for(state="visible", timeout=5000)
                ok_button.click()
                logger.debug("google auth OK button clicker")
            except PlaywrightTimeoutError:
                pass

            page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
            logger.info("Login successful, navigating to user management page.")
            ss = capture_and_upload_screenshot(page=page, backend=BACKEND_NAME, task_id=task_id)
            logger.debug(f"debug screenshot captured: {ss}")

            try:
                # Element-UI message box is NOT a role="dialog", so rely on structure + text
                notification = page.locator(
                    "div.el-message-box"
                ).filter(
                    has_text="You have slightly low balance"
                )

                # Wait briefly — this dialog is usually fast if it appears
                notification.wait_for(state="visible", timeout=2000)

                # Click the OK button *within this message box only*
                notification.locator("button", has_text="OK").click()

                logger.info("Low balance notification detected and dismissed")

            except PlaywrightTimeoutError:
                # Notification did not appear — safe to continue
                logger.info("Low balance notification not present, continuing.")

            try:
                password_change_dialog = page.get_by_role("dialog", name="Hint").filter(
                    has_text="your account password has not been changed for a long time"
                )
                password_change_dialog.wait_for(state="visible", timeout=2000)
                logger.info("Password change reminder dialog detected")

                # Example action: click "Modify next time"
                password_change_dialog.get_by_role(
                    "button", name="Modify next time"
                ).click()

            except PlaywrightTimeoutError:
                logger.info("Password change reminder dialog not present")

            try:
                # 1. Define the dialog by Role + Name AND filter by the specific text content
                # This resolves the strict mode violation by ignoring the other "Hint" dialog.
                dialog = page.get_by_role("dialog", name="Hint").filter(
                    has_text="Your account was logged in from a different location"
                )

                # 2. Wait for this specific dialog to be visible
                dialog.wait_for(state="visible", timeout=3000)

                # 3. Click the Confirm button inside this specific dialog
                dialog.get_by_role("button", name="confirm").click()

                logger.info("Remote login dialog detected and closed.")

            except PlaywrightTimeoutError:
                # Dialog did not appear — safe to continue
                logger.info("Remote login dialog not present, continuing.")

            page.wait_for_timeout(3000)

            try:
                # 1. Define the dialog by Role + Name AND filter by the specific text content
                # This resolves the strict mode violation by ignoring the other "Hint" dialog.
                dialog = page.get_by_role("dialog", name="Hint").filter(
                    has_text="To ensure the security of your account"
                )

                # 2. Wait for this specific dialog to be visible
                dialog.wait_for(state="visible", timeout=3000)

                # 3. Click the Confirm button inside this specific dialog
                dialog.get_by_role("button", name="confirm").click()

                logger.info("google authenticator bind dialog detected and closed")

            except PlaywrightTimeoutError:
                # Dialog did not appear — safe to continue
                logger.info("google authenticator bind dialog not present, continuing.")

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
            update_automation_result(task_id=task_id, status="failed", description="Timeout waiting for session from another task")
            raise Exception("Timeout waiting for session")

        inject_session_token(page, session.token)
        if not validate_session_token(page, logger):
            update_automation_result(task_id=task_id, status="failed", description="Session after wait was invalid")
            raise Exception("Session after wait was invalid")

        logger.info("Session from another task injected and validated.")
        page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)

        page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
        try:
            # Element-UI message box is NOT a role="dialog", so rely on structure + text
            notification = page.locator(
                "div.el-message-box"
            ).filter(
                has_text="You have slightly low balance"
            )

            # Wait briefly — this dialog is usually fast if it appears
            notification.wait_for(state="visible", timeout=2000)

            # Click the OK button *within this message box only*
            notification.locator("button", has_text="OK").click()

            logger.info("Low balance notification detected and dismissed")

        except PlaywrightTimeoutError:
            # Notification did not appear — safe to continue
            logger.info("Low balance notification not present, continuing.")

        try:
            password_change_dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="your account password has not been changed for a long time"
            )
            password_change_dialog.wait_for(state="visible", timeout=2000)
            logger.info("Password change reminder dialog detected")

            # Example action: click "Modify next time"
            password_change_dialog.get_by_role(
                "button", name="Modify next time"
            ).click()

        except PlaywrightTimeoutError:
            logger.info("Password change reminder dialog not present")

        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")

        logger.info("Session from another task injected and validated.")
        return session


def _create_single_account(page: Page, logger: logging.Logger, task_id):
    try:
        # 1. Define the dialog by Role + Name AND filter by the specific text content
        # This resolves the strict mode violation by ignoring the other "Hint" dialog.
        dialog = page.get_by_role("dialog", name="Hint").filter(
            has_text="To ensure the security of your account"
        )

        # 2. Wait for this specific dialog to be visible
        dialog.wait_for(state="visible", timeout=3000)

        # 3. Click the Confirm button inside this specific dialog
        dialog.get_by_role("button", name="confirm").click()

        logger.info("google authenticator bind dialog detected and closed")

    except PlaywrightTimeoutError:
        # Dialog did not appear — safe to continue
        logger.info("google authenticator bind dialog not present, continuing.")
    logger.debug("Opening create account dialog.")
    page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    while True:
        delay = random.randint(1000, 5000)
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


def _recharge_account(page: Page, logger: logging.Logger, points: int, account_id: str, order_id, task_id, wallet_id, amount_to_deduct, coupon_code = None):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)
    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")
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

        # Known response texts (normalized)
        SUCCESS_TEXTS = ["sucessful operation", "successful operation"]
        INSUFFICIENT_TEXT = "not authorized to check remaining balance"

        try:
            # Wait until ANY known recharge response becomes visible
            page.wait_for_function(
                """() => {
                    const knownTexts = [
                        "sucessful operation",
                        "successful operation",
                        "not authorized to check remaining balance"
                    ];

                    return [...document.querySelectorAll("p")]
                        .some(p => {
                            const text = p.innerText?.toLowerCase() || "";
                            const visible = p.offsetParent !== null;
                            return visible && knownTexts.some(t => text.includes(t));
                        });
                }""",
                timeout=25000
            )

            # Collect all visible <p> texts
            visible_texts = [
                p.inner_text().strip().lower()
                for p in page.locator("p").all()
                if p.is_visible()
            ]

            combined_text = " | ".join(visible_texts)

            # ---------------- Default (unexpected) outcome ----------------
            log_type = "warning"
            description = (
                f"Unexpected recharge response: {combined_text} "
                f"on {BACKEND_NAME} - Wallet balance restored"
            )
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
            restore_coupon = True

            bonus_transferred = False

            # ---------------- Backend balance insufficient ----------------
            if any(INSUFFICIENT_TEXT in text for text in visible_texts):
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

            # ---------------- Success ----------------
            elif any(
                    success_text in text
                    for text in visible_texts
                    for success_text in SUCCESS_TEXTS
            ):
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
                restore_coupon = False
                if _.user.bonus_received:
                    bonus_transferred = True

            else:
                logger.warning(f"Unexpected recharge response: {combined_text}")

            # ---------------- Persist result ----------------
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
                bonus_transferred=bonus_transferred,
                restore_coupon=restore_coupon,
                coupon_code=coupon_code,
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
                restore_coupon=True,
                coupon_code=coupon_code,
            )
            logger.info("Wallet balance restored")
        break




def _freeplay_account(page: Page, logger: logging.Logger, points: int, account_id: str, task_id, t, id_to_update, freeplay_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)

    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")
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
            logger.info("No error message, proceeding…")

        table = page.locator(
            "div.el-table",
            has=page.locator("th", has_text="Connect game provider UID")
        ).first

        table.wait_for(timeout=10000)

        row = table.locator(
            "tbody tr",
            has=page.locator("td:nth-child(2) .cell", has_text=account_id)
        ).first

        row.wait_for(timeout=5000)
        balance = row.locator("td:nth-child(10) .cell span").inner_text().strip()
        logger.info(f"Available balance: {balance}")
        if float(balance) >= 5:
            logger.info(f"Available balance is not freeplay eligible. Aborting")
            insert_log_and_update_automation_result(
                log_type="warning",
                log_description="Available balance is not freeplay eligible. Aborting",
                task_id=task_id,
                backend_id=BACKEND_ID,
                source_url=str(page.url),
                account_id=_.id,
                result_status="failed",
                result_data={"balance": balance},
                result_description="Available balance is not freeplay eligible. Aborting",
            )
            return


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

        # Known response texts (normalized to lowercase)
        SUCCESS_TEXTS = ["sucessful operation", "successful operation"]
        INSUFFICIENT_TEXT = "not authorized to check remaining balance"

        try:
            # Wait until ANY known recharge response becomes visible
            page.wait_for_function(
                """() => {
                    const knownTexts = [
                        "sucessful operation",
                        "successful operation",
                        "not authorized to check remaining balance"
                    ];

                    return [...document.querySelectorAll("p")]
                        .some(p => {
                            const text = p.innerText?.toLowerCase() || "";
                            const visible = p.offsetParent !== null;
                            return visible && knownTexts.some(t => text.includes(t));
                        });
                }""",
                timeout=25000
            )

            # Collect all visible <p> texts for deterministic evaluation
            visible_texts = [
                p.inner_text().strip().lower()
                for p in page.locator("p").all()
                if p.is_visible()
            ]

            combined_text = " | ".join(visible_texts)

            # Default values (unchanged behavior)
            log_type = "warning"
            description = f"Unexpected recharge response: {combined_text}"
            result_status = "failed"

            # ---- Failure: Backend balance insufficient ----
            if any(INSUFFICIENT_TEXT in text for text in visible_texts):
                logger.error("Recharge failed: backend balance insufficient.")
                send_email(
                    subject="Recharge failed",
                    body=(
                        f"Recharge failed for account: {account_id} "
                        f"because of insufficient balance on {BACKEND_NAME}."
                    ),
                )
                description = f"Insufficient backend balance for {BACKEND_NAME}"

            # ---- Success ----
            elif any(
                    success_text in text
                    for text in visible_texts
                    for success_text in SUCCESS_TEXTS
            ):
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

            else:
                logger.warning(f"Unexpected recharge response: {combined_text}")

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
            logger.exception("No dialog appeared after setting score.")
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

        break


def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")
    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")
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
            has=page.locator("td:nth-child(2) .cell", has_text=account_id)
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

def _withdraw_account(page: Page, logger: logging.Logger, points: int, account_id: str, task_id, redeem_request_id, order_id, requested_amount):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={points}")
    _ = get_backend_account(account_id)

    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")
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

        # Known response texts (normalized)
        SUCCESS_TEXTS = ["sucessful operation", "successful operation"]
        INSUFFICIENT_TEXT = "cannot exceed current points"

        try:
            # Wait until ANY known withdrawal response becomes visible
            page.wait_for_function(
                """() => {
                    const knownTexts = [
                        "sucessful operation",
                        "successful operation",
                        "cannot exceed current points"
                    ];

                    return [...document.querySelectorAll("p")]
                        .some(p => {
                            const text = p.innerText?.toLowerCase() || "";
                            const visible = p.offsetParent !== null;
                            return visible && knownTexts.some(t => text.includes(t));
                        });
                }""",
                timeout=3000
            )

            # Collect all visible <p> texts
            visible_texts = [
                p.inner_text().strip().lower()
                for p in page.locator("p").all()
                if p.is_visible()
            ]

            combined_text = " | ".join(visible_texts)

            # ---------------- Default values (unexpected / safe failure) ----------------
            log_type = "warning"
            description = f"Unexpected withdrawal response: {combined_text}"
            result_status = "failed"
            redeem_request_status = "failed"

            wallet_detail_status = "failed"
            add_to_wallet = False
            add_to_wallet_amount = requested_amount

            # ---------------- Insufficient balance ----------------
            if any(INSUFFICIENT_TEXT in text for text in visible_texts):
                logger.error("Withdrawal failed due to insufficient gold.")
                description = "Insufficient customer balance."

            # ---------------- Success ----------------
            elif any(
                    success_text in text
                    for text in visible_texts
                    for success_text in SUCCESS_TEXTS
            ):
                logger.info("Withdraw successful.")
                log_type = "info"
                description = f"Withdrawal successful for account: {account_id}"
                result_status = "success"
                redeem_request_status = "processed"

                wallet_detail_status = "finished"
                add_to_wallet = True

            else:
                logger.warning(f"Unexpected withdrawal response: {combined_text}")

            # ---------------- Persist result ----------------
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
                add_to_wallet_amount=add_to_wallet_amount,
            )

        except PlaywrightTimeoutError:
            logger.exception("No dialog appeared after setting score.")
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
                add_to_wallet=False,
            )

        break


def _reset_password(page: Page, logger: logging.Logger, account_id: str, task_id: str):
    logger.info(f"Initiating reset password: account_id={account_id}")
    _ = get_backend_account(account_id)
    __, password = generate_credentials()

    for attempt in range(5):
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            # 1. Define the dialog by Role + Name AND filter by the specific text content
            # This resolves the strict mode violation by ignoring the other "Hint" dialog.
            dialog = page.get_by_role("dialog", name="Hint").filter(
                has_text="To ensure the security of your account"
            )

            # 2. Wait for this specific dialog to be visible
            dialog.wait_for(state="visible", timeout=3000)

            # 3. Click the Confirm button inside this specific dialog
            dialog.get_by_role("button", name="confirm").click()

            logger.info("google authenticator bind dialog detected and closed")

        except PlaywrightTimeoutError:
            # Dialog did not appear — safe to continue
            logger.info("google authenticator bind dialog not present, continuing.")
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

        # Known response texts (normalized)
        SUCCESS_TEXTS = ["sucessful operation", "successful operation"]

        try:
            # Wait until ANY known password reset response becomes visible
            page.wait_for_function(
                """() => {
                    const knownTexts = [
                        "sucessful operation",
                        "successful operation"
                    ];

                    return [...document.querySelectorAll("p")]
                        .some(p => {
                            const text = p.innerText?.toLowerCase() || "";
                            const visible = p.offsetParent !== null;
                            return visible && knownTexts.some(t => text.includes(t));
                        });
                }""",
                timeout=5000
            )

            # Collect all visible <p> texts
            visible_texts = [
                p.inner_text().strip().lower()
                for p in page.locator("p").all()
                if p.is_visible()
            ]

            combined_text = " | ".join(visible_texts)

            # ---------------- Default values (safe failure) ----------------
            log_type = "warning"
            description = f"Password reset failed. Unhandled reset response: {combined_text}"
            result_data: dict | None = None
            result_status = "failed"

            # ---------------- Success ----------------
            if any(
                    success_text in text
                    for text in visible_texts
                    for success_text in SUCCESS_TEXTS
            ):
                logger.info("Password reset successful.")
                log_type = "info"
                description = f"Password reset successful for account {account_id}"
                result_data = {"password": password}
                result_status = "success"

                update_password_by_username(
                    username=account_id,
                    new_password=password
                )

            else:
                logger.warning(
                    f"Password reset failed. Unhandled reset response: {combined_text}"
                )

            # ---------------- Persist result ----------------
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
            increment_active_tasks_count(session.id, logger)
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
        if session:
            decrement_active_tasks_count(session.id, logger)
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url), backend_id=BACKEND_ID, task_id=task_id)

@with_persistent_browser
def action_recharge_account(page: Page, count: int, account_id: str, order_id, task_id, backend, wallet_id, amount_to_deduct, coupon_code = None):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id, logger)
        _recharge_account(page, logger, count, account_id, order_id, task_id, wallet_id, amount_to_deduct, coupon_code)
    except (PlaywrightTimeoutError, Exception) as e:
        restore_wallet_balance(wallet_id, amount_to_deduct, order_id, coupon_code)
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
            log_description=f"WALLET_RESTORED - Error during account recharge: {e}",
            task_id=task_id,
            source_url=str(page.url),
            backend_id=backend_game.id,
            result_status="failed",
            result_description=f"WALLET_RESTORED - Error during account recharge: {e}",
            screenshot_url=screenshot_url,
            account_id=backend_account.id,
        )
    finally:
        decrement_active_tasks_count(session.id, logger)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend, t, id_to_update, freeplay_id):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id, logger)
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
        if session:
            decrement_active_tasks_count(session.id, logger)
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)



@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend, redeem_request_id, order_id, requested_amount):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.",
            source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id, logger)
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
        if session:
            decrement_active_tasks_count(session.id, logger)
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
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id, logger)

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
        if session:
            decrement_active_tasks_count(session.id, logger)
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)



@with_persistent_browser
def action_reset_password(page: Page, account_id: str, task_id, backend):
    backend_game, backend_account = get_backend_and_account(backend, account_id)

    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Reset-password action started: account_id=%s", account_id)

    session = None

    try:
        insert_log(
            "info",
            f"Initiating password reset for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url),
            backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id
        )
        session = _login_and_navigate(page, logger, backend_game, task_id)
        if session:
            increment_active_tasks_count(session.id, logger)
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
        if session:
            decrement_active_tasks_count(session.id, logger)
        logger.info("Reset-password action completed.")
        insert_log("info", "Reset password action completed", source_url=str(page.url), backend_id=backend_game.id, account_id=backend_account.id, task_id=task_id)