from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import logging

def click_purchase_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Purchase button for Username: {account_id}")

    try:
        # 1. Wait for the table to be visible
        table = page.locator("#table-accounts")
        table.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError:
        raise Exception("❌ No table found within timeout.")

    row_xpath = (
        f'//table[@id="table-accounts"]//tr[td[2]//span[contains(text(), "{account_id}")]]'
    )
    row = page.locator(row_xpath).first

    try:
        row.wait_for(timeout=15000)
        logger.debug(f"✅ Found row for account: {account_id}")

        # 4. Find and click the Purchase button inside that row
        purchase_btn = row.locator('button:has-text("Purchase")')
        purchase_btn.click()

        logger.info(f"✅ Clicked Purchase button for Username: {account_id}")
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Row or Purchase button not found for account: {account_id}")


def click_redeem_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Redeem button for Username: {account_id}")

    # 1) Wait for at least one account row to appear
    try:
        table = page.locator("#table-accounts")
        table.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError:
        raise Exception(" No table found within timeout.")

    row_xpath = (
        f'//table[@id="table-accounts"]//tr[td[2]//span[contains(text(), "{account_id}")]]'
    )
    row = page.locator(row_xpath).first

    try:
        row.wait_for(timeout=15000)
        logger.debug(f"✅ Found row for account: {account_id}")

        # 4. Find and click the Purchase button inside that row
        redeem_btn = row.locator('button:has-text("Redeem")')
        redeem_btn.click()

        logger.info(f"✅ Clicked Redeem button for Username: {account_id}")
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Row or Redeem button not found for account: {account_id}")

def click_delete_password_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Delete Password button for Username: {account_id}")

    try:
        # 1. Wait for the accounts table to be visible
        table = page.locator("#table-accounts")
        table.wait_for(state="visible", timeout=20_000)
    except PlaywrightTimeoutError:
        raise Exception("❌ No table found within timeout.")

    row_xpath = (
        f'//table[@id="table-accounts"]//tr[td[2]//span[contains(text(), "{account_id}")]]'
    )
    row = page.locator(row_xpath).first

    try:
        row.wait_for(timeout=15000)
        logger.debug(f"✅ Found row for account: {account_id}")

        # 4. Handle alert when clicking delete password
        def handle_dialog(dialog):
            logger.info(f"⚠️ Alert shown: {dialog.message}")
            page.wait_for_timeout(2000)
            dialog.accept()
            logger.info(f"✅ Alert accepted for delete password.")

        page.once("dialog", handle_dialog)

        # 5. Click the delete password button (11th column)
        delete_pwd_btn = row.locator('td:nth-child(11) a[rel="remove-pwd"]')
        delete_pwd_btn.click()


        logger.info(f"✅ Clicked Delete Password button for Username: {account_id}")
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Row or Delete Password button not found for account: {account_id}")
