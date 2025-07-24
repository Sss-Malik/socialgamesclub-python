# automation_gamevault.py
import json
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.handle_captcha import handle_captcha

from backends.gamevault.config import *
from backends.gamevault.utils.credentials import generate_credentials
from backends.gamevault.utils.actions import click_recharge_for_account
from backends.gamevault.utils.actions import click_redeem_for_account
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username, update_order_automation_status, update_automation_result, mark_freeplay_transferred
from common.utils.browser import with_persistent_browser

from settings import APP_ENV, HEADLESS, DEBUG


def _login_and_navigate(page: Page, logger: logging.Logger, backend, task_id):
    logger.info("Initiating login process.")
    logger.debug("Fetching backend details from db...")

    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL
    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")

    logger.debug("Navigating to login page at: %s", LOGIN_URL)

    page.goto(login_url, wait_until="domcontentloaded")

    try:
        if page.locator(MAIN_PAGE_EL).is_visible(timeout=5_000):
            logger.info("Existing session detected; skipping login.")
            page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
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
            dialog_el = page.locator("p.el-message__content")
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
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)

    logger.info("Login successful, navigating to user management page.")
    page.goto(USER_MANAGEMENT_URL, wait_until="domcontentloaded")
    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")

    try:
        announcement_box = page.locator("div.el-message-box.security-announcement-box")
        announcement_box.wait_for(timeout=2000, state="visible")
        announcement_box.locator("button:has-text('OK')").click()
    except PlaywrightTimeoutError:
        pass


    while True:
        page.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)
        page.locator(ACCOUNT_ID).wait_for(timeout=10_000)

        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        page.locator(ACCOUNT_ID).fill(account_id)
        page.locator(ACCOUNT_PASSWORD).fill(password)
        page.locator(CONFIRM_PASSWORD).fill(password)


        page.locator(CREATE_ACCOUNT).click()

        page.wait_for_timeout(1000)

        # Look for all visible message contents
        messages = page.locator("p.el-message__content").all()

        should_restart = False
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "login name have used" in text or "form is being submitted" in text:
                    logger.warning("⚠️ Detected message: %r — restarting account creation.", text)
                    should_restart = True
                    break

        if should_restart:
            close_btn = page.locator(
                ".el-dialog:has(.el-dialog__title:text('Essential information')) .el-dialog__headerbtn")
            if close_btn.is_visible():
                close_btn.click()
                logger.debug("Closed 'Essential information' dialog.")
            else:
                logger.debug("'Essential information' dialog close button not visible.")
            page.wait_for_timeout(1000)
            continue

        try:
            dlg = page.locator(".el-dialog:has(#invoiceModel)")
            dlg.wait_for(timeout=10000, state="visible")
            text = dlg.inner_text().strip().lower()
            if "successfully" in text:
                logger.info("Account created successfully: %s", account_id)
                save_credentials(account_id, password, logger, DATA_DIR)
                insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                break
            else:
                logger.warning(f"Unexpected message after creating account: {text}")
                insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url))
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url))
            break


def _read_account(page: Page, logger: logging.Logger, account_id: str, task_id):
    logger.info(f"Reading account info: {account_id}")

    try:
        announcement_box = page.locator("div.el-message-box.security-announcement-box")
        announcement_box.wait_for(timeout=2000, state="visible")
        announcement_box.locator("button:has-text('OK')").click()
    except PlaywrightTimeoutError:
        pass

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    row = page.locator(
        "table.el-table__body tbody tr"
    ).filter(
        has=page.locator(f"td .cell:text('{account_id}')")
    ).first

    row.wait_for(timeout=5000)
    logger.debug("Account row located in table.")
    backend_account_id = row.locator("td:nth-child(2) .cell").inner_text().strip()
    data = {
        "id": row.locator("td:nth-child(2) .cell").inner_text().strip(),
        "account": row.locator("td:nth-child(4) .cell").inner_text().strip(),
        "balance": row.locator("td:nth-child(5) .cell").inner_text().strip(),
        "created_at": row.locator("td:nth-child(7) .cell").inner_text().strip(),
        "login_count": row.locator("td:nth-child(9) .cell").inner_text().strip(),
        "last_login": row.locator("td:nth-child(10) .cell").inner_text().strip(),
        "last_login_ip": row.locator("td:nth-child(11) .cell").inner_text().strip(),
    }
    update_game_id_by_username(account_id, backend_account_id)
    update_automation_result(task_id=task_id, status="success", data=json.dumps(data), description="Account information")
    logger.info(f"Account read data: {data}")


def _recharge_account(page: Page, logger: logging.Logger, amount: int, account_id: str, order_id, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={amount}")

    try:
        announcement_box = page.locator("div.el-message-box.security-announcement-box")
        announcement_box.wait_for(timeout=2000, state="visible")
        announcement_box.locator("button:has-text('OK')").click()
    except PlaywrightTimeoutError:
        pass

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)

    # fill amount
    page.locator("//label[text()='Recharge Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    # confirm dialog
    dlg = page.locator("div.el-dialog",
                      has=page.locator("span.el-dialog__title", has_text="Please confirm your recharge & details!"))
    confirm_btn = dlg.locator(".el-dialog__footer button.el-button--primary", has_text="Confirm")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    messages = page.locator("p.el-message__content").all()
    for msg in messages:
        if msg.is_visible():
            text = msg.inner_text().strip().lower()
            if "not enougn balance" in text:
                logger.error("Recharge failed: backend balance insufficient.")
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
            if "form is being submitted" in text:
                update_automation_result(task_id=task_id, status="failed", description=f"Form submission error on {BACKEND_NAME}")
                return

    # verify deposit
    try:
        invoice = page.locator("#invoiceModel")
        invoice.wait_for(timeout=25000, state="visible")
        deposit = invoice.locator("p", has=page.locator("label", has_text="DEPOSIT:"))
        deposit.wait_for(timeout=5_000, state="visible")
        txt = deposit.inner_text().strip().lower()
        if txt.startswith("deposit:") and any(ch.isdigit() for ch in txt):
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
            update_order_automation_status(order_id, "finished")
            update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
        else:
            logger.warning(f"Unexpected recharge response: {txt}")
            insert_log("warning", f"Unexpected recharge response: {txt}", source_url=str(page.url))
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url))
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")



def _freeplay_account(page: Page, logger: logging.Logger, amount: int, account_id: str, task_id):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={amount}")

    try:
        announcement_box = page.locator("div.el-message-box.security-announcement-box")
        announcement_box.wait_for(timeout=2000, state="visible")
        announcement_box.locator("button:has-text('OK')").click()
    except PlaywrightTimeoutError:
        pass

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()

    click_recharge_for_account(page, account_id, logger)

    # fill amount
    page.locator("//label[text()='Recharge Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    # confirm dialog
    dlg = page.locator("div.el-dialog",
                      has=page.locator("span.el-dialog__title", has_text="Please confirm your recharge & details!"))
    confirm_btn = dlg.locator(".el-dialog__footer button.el-button--primary", has_text="Confirm")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    messages = page.locator("p.el-message__content").all()
    for msg in messages:
        if msg.is_visible():
            text = msg.inner_text().strip().lower()
            if "not enougn balance" in text:
                logger.error("Recharge failed: backend balance insufficient.")
                update_automation_result(task_id=task_id, status="failed", description=f"Insufficient backend balance on {BACKEND_NAME}")
                raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
            if "form is being submitted" in text:
                update_automation_result(task_id=task_id, status="failed", description=f"Form submission error on {BACKEND_NAME}")
                return

    # verify deposit
    try:
        invoice = page.locator("#invoiceModel")
        invoice.wait_for(timeout=25000, state="visible")
        deposit = invoice.locator("p", has=page.locator("label", has_text="DEPOSIT:"))
        deposit.wait_for(timeout=5_000, state="visible")
        txt = deposit.inner_text().strip().lower()
        if txt.startswith("deposit:") and any(ch.isdigit() for ch in txt):
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
            update_automation_result(task_id=task_id, status="success", description="Recharge successful.")
            mark_freeplay_transferred(account_id)
        else:
            logger.warning(f"Unexpected recharge response: {txt}")
            insert_log("warning", f"Unexpected recharge response: {txt}", source_url=str(page.url))
            update_automation_result(task_id=task_id, status="failed", description=f"Unexpected recharge response on {BACKEND_NAME}")
    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url))
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after recharge on {BACKEND_NAME}")



def _withdraw_account(page: Page, logger: logging.Logger, amount: int, account_id: str, task_id):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={amount}")

    try:
        announcement_box = page.locator("div.el-message-box.security-announcement-box")
        announcement_box.wait_for(timeout=2000, state="visible")
        announcement_box.locator("button:has-text('OK')").click()
    except PlaywrightTimeoutError:
        pass

    page.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    page.locator("button:has-text('search')").click()
    logger.debug("Calling click_redeem_for_account helper.")
    click_redeem_for_account(page, account_id, logger)

    # confirm dialog
    dlg = page.locator("div.el-dialog",
                       has=page.locator("span.el-dialog__title", has_text="Please confirm your redeem & details!"))

    dlg.wait_for(timeout=10_000, state="visible")

    # fill amount
    dlg.locator("//label[text()='Redeem Amount']/following-sibling::div//input")\
        .fill(str(amount))

    if DEBUG:
        input("Debug mode: press enter to continue withdrawal.")

    confirm_btn = dlg.locator(".el-dialog__footer button.el-button--primary", has_text="Confirm")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    confirm_btn.click()

    page.wait_for_timeout(1000)

    # verify withdraw
    try:
        messages = page.locator("p.el-message__content").all()
        for msg in messages:
            if msg.is_visible():
                text = msg.inner_text().strip().lower()
                if "the redeem amount can not be greater than the balance on the body！" in text:
                    logger.error("Withdrawal failed due to insufficient gold.")
                    update_automation_result(task_id=task_id, status="failed", description="Insufficient customer balance.")
                    raise Exception(f"Insufficient customer balance for withdrawal: {account_id}, backend: {BACKEND_NAME}")
                elif "success" in text:
                    logger.info("Withdraw successful.")
                    update_automation_result(task_id=task_id, status="success", description="Withdraw successful.")
                    insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url))
                else:
                    logger.warning(f"Unexpected withdrawal response: {text}")
                    update_automation_result(task_id=task_id, status="failed", description=f"Unexpected withdrawal response on {BACKEND_NAME}")
                    insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url))
    except PlaywrightTimeoutError:
        logger.error("Failed to detect result dialog after account withdrawal.")
        update_automation_result(task_id=task_id, status="failed", description=f"Failed to detect result after withdraw on {BACKEND_NAME}")
        insert_log("warning", "Failed to detect dialog after account withdrawal", source_url=str(page.url))



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
        update_automation_result(task_id=task_id, description=f"Error during account creation. {e}", status="failed")
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url))

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
        update_automation_result(task_id=task_id, description=f"Error during account recharge. {e}", status="failed")
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url))


@with_persistent_browser
def action_freeplay_account(page: Page, count: int, account_id: str, task_id, backend):
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
        _freeplay_account(page, logger, count, account_id, task_id)
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log(
            "error",
            f"Error during account recharge: {e}",
            source_url=str(page.url),
        )
        update_automation_result(task_id=task_id, description=f"Error during account recharge. {e}", status="failed")
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url))


@with_persistent_browser
def action_withdraw_account(page: Page, count: int, account_id: str, task_id, backend):
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
        update_automation_result(task_id=task_id, description=f"Error during account withdrawal. {e}", status="failed")
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url))

@with_persistent_browser
def action_read_account(page: Page, account_id: str, task_id, backend):
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
        update_automation_result(task_id=task_id, description=f"Error during account read. {e}", status="failed")
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url))

