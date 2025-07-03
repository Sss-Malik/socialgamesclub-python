import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.gameroom.config import *
from backends.gameroom.utils.credentials import generate_credentials
from backends.gameroom.utils.actions import click_recharge_for_account
from backends.gameroom.utils.actions import click_withdraw_for_account

from common.utils.logger import get_backend_logger
from common.utils.ensure_directories import ensure_directories
from common.utils.handle_captcha import handle_captcha
from common.utils.save_credentials import save_credentials
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username
from common.utils.browser import with_browser

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend):
    logger.info("Initiating login process.")
    logger.debug("Fetching backend details from db...")
    
    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")

    logger.debug("Navigating to login page at: %s", LOGIN_URL)
    page.goto(login_url, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap_in = page.locator(CAPTCHA_INPUT)
    btn   = page.locator(LOGIN_BUTTON)

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
                raise Exception(f"Incorrect login credentials for backend: {backend.name}")
            else:
                logger.info(f"Unknown dialog message: {text}")
                break
        except PlaywrightTimeoutError:
            logger.info("Login likely successful (no error dialog detected).")

            break

    logger.info("Login successful, navigating to user management page.")
    page.locator(MAIN_PAGE_EL).wait_for(state="attached", timeout=20_000)

    game_user = page.locator('a', has_text="Game User")
    game_user.wait_for(state="visible", timeout=20_000)
    game_user.click()

    user_mgmt = page.locator(USER_MANAGEMENT_EL)
    user_mgmt.wait_for(state="visible", timeout=20_000)
    user_mgmt.click()
    logger.info("Login and navigation successful.")



def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
    main_iframe = page.frame_locator(MAIN_IFRAME)
    main_iframe.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    dialog_iframe = main_iframe.frame_locator(DIALOG_IFRAME)
    dialog_iframe.locator(ACCOUNT_ID).wait_for(timeout=10_000)

    while True:
        account_id, password = generate_credentials()
        logger.debug(f"Generated credentials: {account_id} / {password}")

        dialog_iframe.locator(ACCOUNT_ID).fill(account_id)
        dialog_iframe.locator(ACCOUNT_BALANCE).fill("0")
        dialog_iframe.locator(ACCOUNT_PASSWORD).fill(password)
        dialog_iframe.locator(CONFIRM_PASSWORD).fill(password)
        dialog_iframe.locator(CREATE_ACCOUNT).click()

        try:
            # wait for the post‐submit message
            msg = dialog_iframe.locator(ACCOUNT_SUCCESS)
            msg.wait_for(state="visible", timeout=10_000)
            text = msg.inner_text().strip().lower()
            
            if "username already exists" in text:
                logger.warning(f"Account ID already exists: {account_id}")
                continue
            elif "successful" in text:
                logger.info("Account created successfully: %s", account_id)
                insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                save_credentials(account_id, password, logger, DATA_DIR)
                page.wait_for_timeout(1_000)
                main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
                break
            else:
                logger.warning(f"Unexpected message after creating account: {text}")
                insert_log("warning", f"Unexpected create account response: {text}", source_url=str(page.url))
                break

        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url))
            break


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
    main_iframe = page.frame_locator(MAIN_IFRAME)

    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    logger.debug("Calling click_withdraw_for_account helper.")
    click_withdraw_for_account(frame_obj, account_id, logger)

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
        result.wait_for(timeout=5_000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Withdraw successful.")
            insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url))
        elif "withdrawal amount is greater than customer balance" in text:
            logger.error("Withdrawal failed due to insufficient gold.")
            raise Exception(f"Insufficient customer balance for withdrawal: {account_id}, backend: {BACKEND_NAME}")
        else:
            logger.warning(f"Unexpected withdrawal response: {text}")
            insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url))
    except PlaywrightTimeoutError:
        logger.error("Failed to detect result dialog after account withdrawal.")
        insert_log("warning", "Failed to detect dialog after account withdrawal", source_url=str(page.url))



def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.info(f"Reading account info: {account_id}")
    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    table = main_iframe.locator("div.layui-table-body.layui-table-main table.layui-table")
    table.wait_for(timeout=5000, state="visible")

    row = main_iframe.locator(
        "div.layui-table-body.layui-table-main table.layui-table > tbody > tr"
    ).filter(
        has=main_iframe.locator(f"td[data-field='Account'] >> text='{account_id}'")
    ).first

    row.wait_for(timeout=5000)
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
    logger.info(f"Account read data: {data}")


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    click_recharge_for_account(frame_obj, account_id, logger)

    # fill & submit recharge form
    recharge_iframe = main_iframe.frame_locator('iframe[src*="recharge"]')
    recharge_iframe.locator('input[name="balance"]').fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge_iframe.locator("button:has-text('Submit')").click()

    # wait for confirmation
    try:
        result = recharge_iframe.locator(ACCOUNT_RECHARGE_SUCCESS)
        result.wait_for(timeout=10_000)
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("Recharge successful.")
            insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
            main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
        elif "recharge balance is greater than available balance" in text:
            logger.error("Recharge failed: backend balance insufficient.")
            raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
        else:
            logger.warning(f"Unexpected recharge response: {text}")
            insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url))
    except PlaywrightTimeoutError:
        logger.error("No recharge confirmation dialog appeared.")
        insert_log("warning", f"Failed to detect dialog after recharge for account: {account_id}", source_url=str(page.url))


@with_browser
def action_create_account(page: Page):
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
        _login_and_navigate(page, logger, backend)
        for i in range(count):
            logger.info("Creating account %d of %d", i + 1, count)
            _create_single_account(page, logger)
            page.reload(wait_until="domcontentloaded")
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
def action_recharge_account(page: Page, count: int, account_id: str):
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
        _login_and_navigate(page, logger, backend)
        _recharge_account(page, logger, count, account_id)
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
def action_withdraw_account(page: Page, count: int, account_id: str):
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
        _login_and_navigate(page, logger, backend)
        _withdraw_account(page, logger, count, account_id)
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
def action_read_account(page: Page, account_id: str):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

    try:
        insert_log(
            "info",
            f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url)
        )
        _login_and_navigate(page, logger, backend)
        _read_account(page, logger, account_id)
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

