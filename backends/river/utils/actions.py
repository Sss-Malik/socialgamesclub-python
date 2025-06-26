from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import logging

def click_purchase_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Purchase button for Username: {account_id}")

    # 1) Wait for at least one account row to appear
    try:
        table = page.locator("#table-accounts")
        table.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError:
        raise Exception(" No table found within timeout.")

    purchase_btn = table.locator('button:has-text("Purchase")')
    purchase_btn.click()
    logger.info(f"✅ Clicked Purchase button for Username: {account_id}")
    return

def click_redeem_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Redeem button for Username: {account_id}")

    # 1) Wait for at least one account row to appear
    try:
        table = page.locator("#table-accounts")
        table.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError:
        raise Exception(" No table found within timeout.")

    purchase_btn = table.locator('button:has-text("Redeem")')
    purchase_btn.click()
    logger.info(f"✅ Clicked Redeem button for Username: {account_id}")
    return
