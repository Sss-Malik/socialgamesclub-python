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

def _login_and_navigate(page: Page, logger: logging.Logger, backend):
    logger.info("Starting login process.")
    logger.debug("Fetching backend details from db...")

    username = backend.username or USERNAME
    password = backend.password or PASSWORD
    login_url = backend.backend_url or LOGIN_URL

    logger.debug(f"Using credentials -> username: {username}, login_url: {login_url}")
    logger.debug("Navigating to login page at: %s", LOGIN_URL)

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
            page.locator("#mb_con").wait_for(timeout=20_000)
            message = page.locator("#mb_msg").inner_text().lower()

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
            else:
                logger.warning(f"Unexpected message after creating account: {message}")
                insert_log("warning", f"Unexpected create account response: {message}", source_url=str(page.url))
                break
        except PlaywrightTimeoutError:
            logger.error("Failed to detect result dialog after account creation.")
            insert_log("warning", "Failed to detect dialog after creating account", source_url=str(page.url))
            break



def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating recharge: account_id={account_id}, amount={count}")
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

    page.locator("#mb_con").wait_for(timeout=5_000)
    text = page.locator("#mb_con").inner_text().lower()
    if "successful" in text:
        logger.info("Recharge successful.")
        insert_log("info", f"Recharge successful for account: {account_id}", source_url=str(page.url))
    elif "insufficient" in text:
        logger.error("Recharge failed: backend balance insufficient.")
        raise Exception(f"Insufficient backend balance for recharge: {account_id}, backend: {BACKEND_NAME}")
    else:
        logger.warning(f"Unexpected recharge response: {text}")
        insert_log("warning", f"Unexpected recharge response: {text}", source_url=str(page.url))

def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    logger.info(f"Initiating withdrawal: account_id={account_id}, amount={count}")
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
        raise Exception(f"Insufficient customer balance for withdrawal: {account_id}, backend: {BACKEND_NAME}")

    redeem.locator("input#txtAddGold").fill(str(count))

    if DEBUG:
        input("Debug mode activated; press enter to continue...")

    redeem.locator('input[type="button"][value="Redeem"]').click()

    page.locator("#mb_con").wait_for(timeout=10_000)
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

def _read_account(page: Page, logger: logging.Logger, account_id: str):
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
        logger.info(f"Account read data: {data}")

def action_create_account():
    backend = get_backend(BACKEND_NAME)
    count = int(backend.accounts_creation_pd)
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

            insert_log("info",f"Initiating account creation for backend '{BACKEND_NAME}' with count {count}.", source_url=str(page.url))
            _login_and_navigate(page, logger, backend)
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
        insert_log("info", "Create account action completed", source_url=str(page.url))

def action_recharge_account(count: int, account_id: str):
    backend = get_backend(BACKEND_NAME)
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

            insert_log("info", f"Initiating withdrawal for account ID {account_id} on backend '{BACKEND_NAME}' with count {count}.", source_url=str(page.url))

            _login_and_navigate(page, logger, backend)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account withdrawal: %s", e, exc_info=True)
        insert_log("error", f"Error during account withdrawal: {e}", source_url=str(page.url))
    finally:
        logger.info("Withdraw-account action completed.")
        insert_log("info", "Withdrawal account action completed", source_url=str(page.url))

def action_read_account(account_id: str):
    backend = get_backend(BACKEND_NAME)
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

            insert_log("info", f"Initiating read for account ID {account_id} on backend '{BACKEND_NAME}'", source_url=str(page.url))

            _login_and_navigate(page, logger, backend)
            _read_account(page, logger, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.critical("Error during account read: %s", e, exc_info=True)
        insert_log("error", f"Error during account read: {e}", source_url=str(page.url))
    finally:
        logger.info("Read-account action completed.")
        insert_log("info", "Read account action completed", source_url=str(page.url))
