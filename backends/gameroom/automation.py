# automation_gameroom.py
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


def _login_and_navigate(page: Page, logger: logging.Logger):
    logger.info("🚀 Launching and navigating to %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap_in = page.locator(CAPTCHA_INPUT)
    btn   = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Login attempt #{attempt+1}")
        acct.fill(USERNAME)
        pwd.fill(PASSWORD)

        logger.debug("Solving CAPTCHA…")
        text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
        if not text or text == 0:
            page.reload(wait_until="domcontentloaded")
            continue

        cap_in.fill(text)
        if DEBUG:
            input("DEBUG: verify CAPTCHA on screen, then press Enter…")

        btn.click()

        try:
            # look for “incorrect” banner
            page.locator("div.layui-layer-content", has_text="incorrect").wait_for(timeout=3_000)
            logger.warning("❗ CAPTCHA incorrect, retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.info("CAPTCHA accepted.")
            break

    # main‐page loaded
    page.locator(MAIN_PAGE_EL).wait_for(state="attached", timeout=20_000)
    logger.info("✅ Logged in successfully.")

    game_user = page.locator('a', has_text="Game User")
    game_user.wait_for(state="visible", timeout=20_000)
    game_user.click()

    user_mgmt = page.locator(USER_MANAGEMENT_EL)
    user_mgmt.wait_for(state="visible", timeout=5_000)
    user_mgmt.click()


def _create_single_account(page: Page, logger: logging.Logger):
    main_iframe = page.frame_locator(MAIN_IFRAME)
    main_iframe.locator(CREATE_ACCOUNT_INIT).click(timeout=15_000)

    dialog_iframe = main_iframe.frame_locator(DIALOG_IFRAME)
    dialog_iframe.locator(ACCOUNT_ID).wait_for(timeout=10_000)

    while True:
        account_id, password = generate_credentials()
        logger.info("🔑 Generated credentials: %s / [REDACTED]", account_id)

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
                logger.info("🔁 Username exists, retrying…")
                continue
            elif "successful" in text:
                logger.info("✅ Account created successfully.")
                save_credentials(account_id, password, logger, DATA_DIR)
                page.wait_for_timeout(1_000)
                main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
                break
            else:
                logger.warning("⚠️ Unexpected response: %r", text)
                break

        except PlaywrightTimeoutError:
            logger.warning("⚠️ No success message after submit, moving on.")
            break


def _withdraw_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # locate main iframe once
    main_iframe = page.frame_locator(MAIN_IFRAME)

    # search
    main_iframe.locator(ACCOUNT_SEARCH_INPUT).fill(account_id)
    main_iframe.locator("button:has-text('Search')").click()

    # call your existing helper (which still expects a Frame object)
    frame_el = page.locator(MAIN_IFRAME).element_handle()
    frame_obj = frame_el.content_frame()
    click_withdraw_for_account(frame_obj, account_id, logger)

    page.wait_for_timeout(1000)

    # fill & submit recharge form
    withdraw_iframe = main_iframe.frame_locator('iframe[src*="withdraw"]')

    withdraw_iframe.locator("div.layui-form-item:has(label:text('Withdraw Balance')) input").fill(str(count))
    if DEBUG:
        input("DEBUG: review withdraw form, then press Enter…")
    withdraw_iframe.locator("button:has-text('Submit')").click()

    # wait for confirmation
    try:
        result = withdraw_iframe.locator("div.layui-layer.layui-layer-dialog")
        result.wait_for(timeout=5_000, state="visible")
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("✅ Account withdraw successful.")
        elif "withdrawal amount is greater than customer balance" in text:
            logger.info("Customer balance insufficient")
        else:
            logger.warning("⚠️ Unexpected withdraw message: %r", text)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No withdraw confirmation dialog appeared.")



def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # locate main iframe once
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
        input("DEBUG: review recharge form, then press Enter…")
    recharge_iframe.locator("button:has-text('Submit')").click()

    # wait for confirmation
    try:
        result = recharge_iframe.locator(ACCOUNT_RECHARGE_SUCCESS)
        result.wait_for(timeout=5_000)
        text = result.inner_text().strip().lower()
        if "successful" in text:
            logger.info("✅ Account deposit successful.")
            main_iframe.locator(ACCOUNT_SUCCESS_CLOSE).click()
        elif "recharge balance is greater than available balance" in text:
            logger.info("Account recharge balance is greater than available balance.")
        else:
            logger.warning("⚠️ Unexpected recharge message: %r", text)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No recharge confirmation dialog appeared.")


def action_create_account(count: int):
    ensure_directories(DATA_DIR, LOGS_DIR, CAPTCHA_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("🏷️  Starting create‐account action: count=%d", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)

            for i in range(count):
                logger.info("🔨 Creating account %d of %d", i + 1, count)
                page.wait_for_timeout(2_000)
                _create_single_account(page, logger)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account creation: %s", e)
    finally:
        logger.info("🏁 Create‐account action completed.")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("💸 Starting recharge‐account: %s → %d", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account recharge: %s", e)
    finally:
        logger.info("🏁 Recharge‐account action completed.")


def action_withdraw_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("💸 Starting withdraw‐account: %s → %d", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            _withdraw_account(page, logger, count, account_id)

            browser.close()
    except (PlaywrightTimeoutError, Exception) as e:
        logger.exception("❌ Error during account recharge: %s", e)
    finally:
        logger.info("🏁 Withdraw‐account action completed.")

