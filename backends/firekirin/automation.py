import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.firekirin.config import *
from backends.firekirin.utils.credentials import generate_credentials
from backends.firekirin.utils.actions import click_update_for_account

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha
from common.utils.db_actions import get_backend, insert_backend_account, insert_log, update_game_id_by_username

from settings import APP_ENV, HEADLESS, DEBUG

def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("Fetching backend details from db...")
    backend = get_backend(BACKEND_NAME)
    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(login_url, wait_until="domcontentloaded")


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
            input("Debug mode activated. Solve CAPTCHA manually and press enter to proceed")
        else:
            text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
            if not text or text == 0:
                logger.warning("CAPTCHA solver returned empty or 0 value: %s", text)
                page.reload(wait_until="domcontentloaded")
                continue
            captcha_input.fill(text)

        login_button.click()

        try:
            page.locator("div#mb_con", has_text="incorrect").wait_for(timeout=5000)
            page.locator("input#mb_btn_ok").click()
            logger.warning("CAPTCHA incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
            continue
        except PlaywrightTimeoutError:
            logger.info("CAPTCHA accepted.")
            break

    logger.debug("Waiting for main page element after login.")
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=20_000)
    logger.info("Login and navigation successful.")

def _create_single_account(page: Page, logger: logging.Logger):
    logger.debug("Initiating create account dialog.")
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
            page.locator("#mb_con").wait_for(timeout=20_000)
            message = page.locator("#mb_msg").inner_text().lower()

            if "already exists" in message:
                logger.info("Account ID already exists: %s", account_id)
                page.locator("#mb_btn_ok").click()
                continue
            elif "success" in message:
                logger.info("Account created successfully: %s", account_id)
                insert_backend_account(username=account_id, password=password, backend_id=BACKEND_ID)
                save_credentials(account_id, password, logger, DATA_DIR)
                page.locator("#mb_btn_ok").click()
                break
            else:
                logger.warning("Unexpected message after creating account: %r", message)
                insert_log("warning", f"Unexpected create account response: {message}")
                break
        except PlaywrightTimeoutError:
            logger.exception("No message after creating account")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url))
            break



def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting recharge for account: {account_id} with count: {count}")
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
        input("Debug mode activated; press enter to continue...")

    recharge.locator('input[type="button"][value="Recharge"]').click()

    page.locator("#mb_con").wait_for(timeout=5_000)
    text = page.locator("#mb_con").inner_text().lower()
    if "successful" in text:
        logger.info("Account recharge successful.")
    elif "insufficient" in text:
        logger.error("Backend balance insufficient.")
        raise Exception("Backend balance insufficient.")
    else:
        logger.warning("Unexpected recharge response: %r", text)
        insert_log("warning", f"Unexpected recharge response: {text} for account: {account_id}")

def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.debug(f"Starting withdraw for account: {account_id} with count: {count}")
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
        logger.error("Customer balance insufficient for withdrawal.")
        raise Exception("Customer balance insufficient for withdrawal.")

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated; press enter to continue...")

    redeem.locator('input[type="button"][value="Redeem"]').click()

    page.locator("#mb_con").wait_for(timeout=10_000)
    text = page.locator("#mb_con").inner_text().lower().strip()
    if "successful" in text:
        logger.info("Withdraw successful.")
    elif "not enough gold" in text:
        logger.error("Withdraw failed due to insufficient gold.")
    else:
        logger.warning("Unexpected withdraw response: %r", text)
        insert_log("warning", f"Unexpected withdraw response: {text} for account: {account_id}")

def _read_account(page: Page, logger: logging.Logger, account_id: str):
    logger.debug(f"Reading account: {account_id}")
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    table = main.locator("table#item")
    table.wait_for(timeout=20_000, state="visible")

    row = table.locator(f"//tr[contains(@class, 'list')][td[3][normalize-space(text())='{account_id}']]").first
    row.wait_for(timeout=10_000)
    if row.is_visible():
        logger.debug("Account row is visible.")
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
        logger.info("Extracted account data: %s", data)

def action_create_account(count: int):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Create-account action started for %d accounts.", count)

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

            insert_log("info",f"Starting account creation for backend '{BACKEND_NAME}' with count {count}.")

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("Creating account %d of %d", i + 1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account creation: %s", e, exc_info=True)
        insert_log("error", f"Error during account creation: {e}", source_url=str(page.url))
    finally:
        logger.info("Create-account action completed.")
        insert_log("info", "Create account action completed")

def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Recharge-account action started: account_id=%s, count=%d", account_id, count)

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

            insert_log("info",f"Starting recharge for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.")

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account recharge: %s", e, exc_info=True)
        insert_log("error", f"Error during account recharge: {e}", source_url=str(page.url))
    finally:
        logger.info("Recharge-account action completed.")
        insert_log("info", "Recharge account action completed")

def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Withdraw-account action started: account_id=%s, count=%d", account_id, count)

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

            insert_log("info", f"Starting withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.")

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log("error", f"Error during account withdrawal: {e}", source_url=str(page.url))
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed")

def action_read_account(account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("Read-account action started: account_id=%s", account_id)

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

            insert_log("info", f"Starting read for account ID {account_id} on backend '{BACKEND_NAME}'")

            _login_and_navigate(page, logger)
            _read_account(page, logger, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log("error", f"Error during account read: {e}", source_url=str(page.url))
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed")
