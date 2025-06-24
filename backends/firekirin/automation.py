# automation.py
import logging
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.firekirin.config import *
from backends.firekirin.utils.credentials import generate_credentials
from backends.firekirin.utils.actions import click_update_for_account

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha


def _login_and_navigate(page: Page, logger: logging.Logger):
    # go to login
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    account_input = page.locator(LOGIN_ACCOUNT)
    password_input = page.locator(LOGIN_PASSWORD)
    captcha_input = page.locator(CAPTCHA_INPUT)
    login_button = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt + 1}")
        account_input.fill(USERNAME)
        password_input.fill(PASSWORD)

        logger.debug("Solving CAPTCHA…")
        text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)

        # if our solver returned no text, try reloading
        if not text or text == 0:
            page.reload(wait_until="domcontentloaded")
            continue

        captcha_input.fill(text)

        if DEBUG:
            input("DEBUG MODE: Verify CAPTCHA then hit Enter…")

        login_button.click()

        # check for “incorrect” message
        try:
            # this will time out quickly if no “incorrect” banner shows
            page.locator("div#mb_con", has_text="incorrect").wait_for(timeout=3_000)
            logger.warning("CAPTCHA was incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
            continue
        except PlaywrightTimeoutError:
            logger.info("CAPTCHA accepted.")
            break

    # wait for main page to load
    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)

    # click “User Management” in the left iframe
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=10_000)

    logger.info("✅ Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    # open the “Create Account” dialog
    page.frame_locator(MAIN_IFRAME).locator(CREATE_ACCOUNT_INIT).click(timeout=10_000)

    # fill the form in the dialog’s iframe
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        account_id, password = generate_credentials()
        dialog.locator(ACCOUNT_ID).fill(account_id)
        dialog.locator(ACCOUNT_PASSWORD).fill(password)
        dialog.locator(CONFIRM_PASSWORD).fill(password)

        dialog.locator(CREATE_ACCOUNT).click()

        # wait for modal feedback
        page.locator("#mb_con").wait_for(timeout=5_000)
        message = page.locator("#mb_msg").inner_text().lower()

        if "already exists" in message:
            logger.info("🔁 Account ID already exists, retrying…")
            page.locator("#mb_btn_ok").click()
            continue
        elif "success" in message:
            logger.info("✅ Account created successfully.")
            save_credentials(account_id, password, logger, DATA_DIR)
            page.locator("#mb_btn_ok").click()
            break
        else:
            logger.warning("⚠️ Unexpected message from server: %r", message)
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # search for the account
    main = page.frame_locator(MAIN_IFRAME)
    main.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main.locator(ACCOUNT_SEARCH_BUTTON).click()
    page.wait_for_timeout(5_000)  # wait for results to show up

    # click the “Update” (via your helper) then Recharge
    # note: click_update_for_account still expects a Frame object, so we grab it here:
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame = frame_el.content_frame()
    click_update_for_account(frame, account_id, logger)

    main.locator("a", has_text="Recharge").click()

    # fill recharge count
    recharge = page.frame_locator('iframe[src*="AccountManager"]')
    recharge.locator("input#txtAddGold").fill(str(count))
    recharge.locator('input[type="button"][value="Recharge"]').click()

    # feedback
    page.locator("#mb_con").wait_for(timeout=5_000)
    text = page.locator("#mb_con").inner_text().lower()
    if "successful" in text:
        logger.info("✅ Account successfully recharged.")
    elif "insufficient" in text:
        logger.warning("⚠️ Backend balance insufficient.")
    else:
        logger.warning("⚠️ Unexpected recharge message: %r", text)


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # search for the account
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
        logger.warning("⚠️ Customer balance insufficient.")
        return

    redeem.locator("input#txtAddGold").fill(str(count))
    redeem.locator('input[type="button"][value="Redeem"]').click()

    # feedback
    page.locator("#mb_con").wait_for(timeout=5_000)
    text = page.locator("#mb_con").inner_text().lower().strip()
    if "successful" in text:
        logger.info("✅ Account successfully redeemed.")
    elif "not enough gold" in text:
        logger.warning("⚠️ Customer balance insufficient.")
    else:
        logger.warning("⚠️ Unexpected redeem message: %r", text)


def _read_account(page: Page, logger: logging.Logger, account_id: str):
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
        logger.info(f"<UNK> Account successfully read.")
        data = {
            "account_id": row.locator("td:nth-child(3)").inner_text().strip(),
            "nickname": row.locator("td:nth-child(4)").inner_text().strip(),
            "balance": row.locator("td:nth-child(5)").inner_text().strip(),
            "register_date": row.locator("td:nth-child(6)").inner_text().strip(),
            "last_login": row.locator("td:nth-child(7)").inner_text().strip(),
            "manager": row.locator("td:nth-child(8)").inner_text().strip(),
            "status": row.locator("td:nth-child(9)").inner_text().strip(),
        }
        logger.info("✅ Extracted row data: %s", data)


def action_create_account(count: int):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting create-account action: count=%d =====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("Creating account %d of %d", i + 1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account creation: %s", e)
    finally:
        logger.info("===== Create-account action completed =====")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting recharge-account action: account_id=%s, count=%d =====",
                account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account recharge: %s", e)
    finally:
        logger.info("===== Recharge-account action completed =====")



def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting withdraw-account action: account_id=%s, count=%d =====",
                account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account recharge: %s", e)
    finally:
        logger.info("===== Withdraw-account action completed =====")

def action_read_account(account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting read-account action: account_id=%s =====",
                account_id)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            _login_and_navigate(page, logger)
            _read_account(page, logger, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account recharge: %s", e)
    finally:
        logger.info("===== Read-account action completed =====")



