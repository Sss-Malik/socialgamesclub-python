from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import logging

def click_purchase_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Purchase button for Username: {account_id}")

    # 1) Wait for at least one account row to appear
    try:
        page.locator("#table-accounts tbody tr[rel='account']").first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        raise Exception(" No account rows found within timeout.")

    # 2) Locate the specific row whose 4th <td> matches the account_id
    row = page.locator(
        "#table-accounts tbody tr[rel='account']",
        has=page.locator("td:nth-child(4)", has_text=account_id)
    ).first

    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        logger.warning(f"⚠️ No row matched Username: {account_id}")
        raise Exception(" No row matched Username.")

    # 3) Within that row, find and click the Purchase button in the 8th cell
    purchase_btn = row.locator("td:nth-child(8) button", has_text="Purchase")
    try:
        purchase_btn.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Purchase button not found for Username: {account_id}")

    purchase_btn.click()
    logger.info(f"✅ Clicked Purchase button for Username: {account_id}")


def click_redeem_for_account(page: Page, account_id: str, logger: logging.Logger):
    logger.debug(f"Searching for Redeem button for Username: {account_id}")

    # 1) Wait for at least one account row to appear
    try:
        page.locator("#table-accounts tbody tr[rel='account']").first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        raise Exception(" No account rows found within timeout.")

    # 2) Locate the specific row whose 4th <td> matches the account_id
    row = page.locator(
        f"//table[@id='table-accounts']//tbody//tr[@rel='account'][td[4][normalize-space(text())='{account_id}']]"
    ).first

    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        logger.warning(f"⚠️ No row matched Username: {account_id}")
        raise Exception(" No row matched Username.")

    # 3) Within that row, find and click the Purchase button in the 8th cell
    purchase_btn = row.locator("td:nth-child(8) button", has_text="Redeem")
    try:
        purchase_btn.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Redeem button not found for Username: {account_id}")

    purchase_btn.click()
    logger.info(f"✅ Clicked Redeem button for Username: {account_id}")
