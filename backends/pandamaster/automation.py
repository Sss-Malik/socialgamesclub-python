# automation_pandamaster.py
import logging
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.pandamaster.config import *
from common.utils.credential_utils import generate_credentials
from backends.pandamaster.utils.actions import click_update_for_account

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username


from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger, backend):
    logger.info("Starting login process.")
    logger.debug("Fetching backend details from db...")
    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")
    logger.debug("Navigating to login page at: %s", LOGIN_URL)


    page.goto(login_url, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap  = page.locator(CAPTCHA_INPUT)
    btn  = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")

        acct.fill(username)
        pwd.fill(password)

        if DEBUG:
            input("Debug mode: Solve CAPTCHA manually and press enter.")
        else:
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
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
                raise Exception(f"Incorrect login credentials for backend: {backend.name}")
        except PlaywrightTimeoutError:
            logger.info("Login likely successful (no error dialog detected).")
            break

    logger.info("Login successful, navigating to user management page.")
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=10_000)
    logger.info("Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Opening create account dialog.")
    page.wait_for_selector(MAIN_IFRAME, timeout=10_000)
    main_frame = page.frame_locator(MAIN_IFRAME)
    create_acc = main_frame.locator(CREATE_ACCOUNT_INIT)
    create_acc.wait_for(timeout=10_000)
    create_acc.click(timeout=10_000)

    page.wait_for_selector(CREATE_ACCOUNT_DIALOG, timeout=15_000)
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        account_id, password = generate_credentials(BACKEND_SIGNATURE)
        logger.debug(f"Generated credentials: {account_id} / {password}")

        dialog.locator(ACCOUNT_ID).fill(account_id)
        dialog.locator(ACCOUNT_PASSWORD).fill(password)
        dialog.locator(CONFIRM_PASSWORD).fill(password)
        dialog.locator(CREATE_ACCOUNT).click()

        # Wait for the feedback modal
        page.locator("#mb_con").wait_for(timeout=10_000)
        msg = page.locator("#mb_msg").inner_text().strip().lower()

        if "already exists" in msg:
            logger.warning(f"Account ID already exists: {account_id}")
            page.locator("#mb_btn_ok").click()
            continue
        elif "success" in msg:
            logger.info("Account created successfully: %s", account_id)
            insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
            save_credentials(account_id, password, logger, DATA_DIR)
            page.locator("#mb_btn_ok").click()
            break
        else:
            logger.warning(f"Unexpected message after creating account: {msg}")
            insert_log("warning", f"Unexpected create account response: {msg}", source_url=str(page.url))
            page.locator("#mb_btn_ok").click()
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")

    main_frame = page.frame_locator(MAIN_IFRAME)
    main_frame.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_frame.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(4000)

    # Click “Update” then “Recharge” via your helper
    click_update_for_account(main_frame, account_id, logger)
    main_frame.locator("a", has_text="Recharge").click(timeout=5000)

    # Fill the recharge amount
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode: press enter to continue recharge.")

    recharge.locator("a", has_text="Recharge").click()

    # Check result
    page.locator("#mb_con").wait_for(timeout=5_000, state="visible")
    result = page.locator("#mb_msg").inner_text().lower()

    if "successful" in result:
        logger.info("Recharge successful.")
        insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
    elif "insufficient" in result:
        logger.error("Recharge failed: backend balance insufficient.")
        raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
    elif "unknown" in result:
        logger.warning("Unknown error.")
        insert_log("warning", f"Unknown error for recharge: {account_id} ", source_url=str(page.url))
    else:
        logger.warning(f"Unexpected recharge response: {result}")
        insert_log("warning", f"Unexpected recharge response: {result}", source_url=str(page.url))



def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.info(f"Reading account info: {account_id}")
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
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
        logger.info(f"Account read data: {data}")

def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")

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
    page.wait_for_timeout(2000)


    # fill recharge count
    redeem = page.frame_locator('iframe[src*="AccountManager"]')
    customer_balance = redeem.locator('input#txtLeScore').get_attribute('value')
    logger.info(f"Extracted value: {customer_balance}")

    if count > float(customer_balance):
        logger.error("Withdraw failed: insufficient customer balance.")
        raise Exception(f"Insufficient customer balance for withdrawal: {account_id}")

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated. Press Enter to continue...")

    redeem.locator('a:has-text("Redeem")').click()

    # feedback
    page.locator("#mb_con").wait_for(timeout=5_000)
    text = page.locator("#mb_con").inner_text().lower().strip()
    if "successful" in text:
        logger.info("Withdraw successful.")
        insert_log("info", f"Withdrawal successful for account: {account_id}", source_url=str(page.url))
    elif "not enough gold" in text:
        logger.error("Withdrawal failed due to insufficient gold.")
        raise Exception(f"Insufficient customer balance for withdrawal: {account_id}, backend: {BACKEND_NAME}")
    else:
        logger.warning(f"Unexpected withdrawal response: {text}")
        insert_log("warning", f"Unexpected withdrawal response: {text}", source_url=str(page.url))



def action_create_account():
    backend = get_backend(BACKEND_NAME)
    count = int(backend.accounts_creation_pd)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Initiating create-account action: count=%d =====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            insert_log("info",f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.", source_url=str(page.url))

            _login_and_navigate(page, logger, backend)
            for i in range(count):
                logger.info("Creating account %d of %d", i+1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
        insert_log("error", f"Error during account creation: {e}", source_url=str(page.url))
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed", source_url=str(page.url))


def action_recharge_account(count: int, account_id: str):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Initiating topup action: account_id=%s, count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            insert_log("info",f"Initiating recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.", source_url=str(page.url))

            _login_and_navigate(page, logger, backend)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log("error", f"Error during account recharge: {e}", source_url=str(page.url))
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed", source_url=str(page.url))


def action_withdraw_account(count: int, account_id: str):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Initiating withdraw-account action: account_id=%s, count=%d =====",
                account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            insert_log("info", f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.", source_url=str(page.url))

            _login_and_navigate(page, logger, backend)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log("error", f"Error during account withdrawal: {e}", source_url=str(page.url))
    finally:
        logger.info("===== Withdraw-account action completed =====")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url))


def action_read_account(account_id: str):
    backend = get_backend(BACKEND_NAME)
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Initiating read-account action: account_id=%s =====",
                account_id)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                ]
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                color_scheme="light",
            )

            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            insert_log("info", f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url))
            _login_and_navigate(page, logger, backend)
            _read_account(page, logger, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log("error", f"Error during account read: {e}", source_url=str(page.url))
    finally:
        logger.info("===== Read-account action completed =====")
        insert_log("info", "Read account action completed", source_url=str(page.url))