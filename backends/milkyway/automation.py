# automation_milkyway.py
import logging
import re
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from backends.milkyway.config import *
from backends.milkyway.utils.credentials import generate_credentials
from backends.milkyway.utils.actions import click_update_for_account

from common.utils.ensure_directories import ensure_directories
from common.utils.save_credentials import save_credentials
from common.utils.logger import get_backend_logger
from common.utils.handle_captcha import handle_captcha


def _login_and_navigate(page: Page, logger: logging.Logger):
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    acct = page.locator(LOGIN_ACCOUNT)
    pwd  = page.locator(LOGIN_PASSWORD)
    cap  = page.locator(CAPTCHA_INPUT)
    btn  = page.locator(LOGIN_BUTTON)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        logger.debug(f"Solving CAPTCHA (attempt {attempt+1})")
        acct.fill(USERNAME)
        pwd.fill(PASSWORD)

        text, solver = handle_captcha(page, logger, CAPTCHA_IMG, CAPTCHA_DIR)
        if not text or text == 0:
            page.reload(wait_until="domcontentloaded")
            continue

        cap.fill(text)
        if DEBUG:
            input("DEBUG MODE: Enter CAPTCHA manually, then press Enter to continue…")

        btn.click()

        try:
            page.locator("div#mb_con", has_text="incorrect").wait_for(timeout=3_000)
            logger.warning("Captcha failed. Retrying…")
            solver.report_incorrect_image_captcha()
            page.reload(wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            logger.info("No captcha-error found; proceeding.")
            break

    page.locator(MAIN_PAGE_EL).wait_for(timeout=20_000)
    page.frame_locator(LEFT_IFRAME).locator(USER_MANAGEMENT_XPATH).click(timeout=10_000)
    logger.info("✅ Login and navigation successful.")


def _create_single_account(page: Page, logger: logging.Logger):
    page.wait_for_selector(MAIN_IFRAME, timeout=10_000)
    main_frame = page.frame_locator(MAIN_IFRAME)
    create_acc = main_frame.locator(CREATE_ACCOUNT_INIT)
    create_acc.wait_for(timeout=10_000)
    create_acc.click(timeout=10_000)

    page.wait_for_selector(CREATE_ACCOUNT_DIALOG, timeout=15_000)
    dialog = page.frame_locator(CREATE_ACCOUNT_DIALOG)

    while True:
        account_id, password = generate_credentials()
        logger.debug(f"Trying credentials: {account_id} / [REDACTED]")

        dialog.locator(ACCOUNT_ID).fill(account_id)
        dialog.locator(ACCOUNT_PASSWORD).fill(password)
        dialog.locator(CONFIRM_PASSWORD).fill(password)
        dialog.locator(CREATE_ACCOUNT).click()

        # Wait for the feedback modal
        page.locator("#mb_con").wait_for(timeout=10_000)
        msg = page.locator("#mb_msg").inner_text().strip().lower()

        if "already exists" in msg:
            logger.info("🔁 Account ID already exists. Retrying…")
            page.locator("#mb_btn_ok").click()
            continue
        elif "success" in msg:
            logger.info("✅ Account created successfully.")
            save_credentials(account_id, password, logger, DATA_DIR)
            page.locator("#mb_btn_ok").click()
            break
        else:
            logger.warning("⚠️ Unexpected message: %r", msg)
            page.locator("#mb_btn_ok").click()
            break


def _recharge_account(page: Page, logger: logging.Logger, count: int, account_id: str):
    # Search in the main iframe
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
    recharge.locator('input[type="button"][value="Recharge"]').click()

    # Check result
    page.locator("#mb_con").wait_for(timeout=5_000, state="visible")
    result = page.locator("#mb_msg").inner_text().lower()

    if "successful" in result:
        logger.info("✅ Account successfully recharged.")
    elif "insufficient" in result:
        logger.warning("⚠️ Backend balance insufficient.")
    elif "unknown" in result:
        logger.warning("Unknown error.")
    else:
        logger.warning("⚠️ Unknown status message: %r", result)


def action_create_account(count: int):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting create-account action: count=%d =====", count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            for i in range(count):
                logger.info("Creating account %d of %d", i+1, count)
                _create_single_account(page, logger)
                page.reload(wait_until="domcontentloaded")

            browser.close()
    except PlaywrightTimeoutError as te:
        logger.exception("Timeout during account creation: %s", te)
    except Exception as e:
        logger.exception("Error during account creation: %s", e)
    finally:
        logger.info("===== Create-account action completed =====")


def action_recharge_account(count: int, account_id: str):
    ensure_directories(DATA_DIR, CAPTCHA_DIR, LOGS_DIR)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info("===== Starting topup action: account_id=%s, count=%d =====", account_id, count)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_context().new_page()

            _login_and_navigate(page, logger)
            _recharge_account(page, logger, count, account_id)

            browser.close()
    except PlaywrightTimeoutError as te:
        logger.exception("Timeout during recharge: %s", te)
    except Exception as e:
        logger.exception("Error during recharge: %s", e)
    finally:
        logger.info("===== Topup-account action completed =====")
