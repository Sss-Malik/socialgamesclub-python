from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

def click_recharge_for_account(page: Page, account_id: str, logger):
    logger.debug(f"Searching for recharge dropdown for Username: {account_id}")

    # 1. Wait for at least one table row to appear
    try:
        page.locator("table.el-table__body tr").first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        raise Exception("No table rows found within timeout.")

    # 2. Locate the specific row whose 4th cell matches account_id
    row = page.locator(
        "table.el-table__body tr",
        has=page.locator("td:nth-child(4) .cell", has_text=account_id.lower())
    ).first

    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"No row matched Username: {account_id}")

    # 3. Click the dropdown trigger in the first cell
    trigger = row.locator("td:nth-child(1) span.el-dropdown-link")
    try:
        trigger.wait_for(timeout=5_000)
        trigger.click()
    except PlaywrightTimeoutError:
        raise Exception(f"No dropdown trigger found for Username: {account_id}")

    # 4. Click the “Recharge” item in the popped‐up menu
    recharge_item = page.locator(
        "ul.el-dropdown-menu.el-popper li.el-dropdown-menu__item",
        has_text="Recharge"
    ).first
    try:
        recharge_item.wait_for(state="visible", timeout=5_000)
        recharge_item.click()
    except PlaywrightTimeoutError:
        raise Exception(f"Recharge menu item not found or not visible for Username: {account_id}")

    logger.info(f"✅ Clicked 'Recharge' from dropdown for Username: {account_id}")

