from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

def click_recharge_for_account(page: Page, account_id: str, logger):
    logger.debug(f"Searching for recharge button for Username: {account_id}")

    # 1. Wait for at least one row to appear in the table body
    try:
        page.locator("table.el-table__body tr").first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No table rows found within timeout.")
        raise Exception("<UNK> No table rows found within timeout.")

    # 2. Find the specific <tr> whose 4th <td> .cell text matches account_id
    row = page.locator(
        f"//table[contains(@class,'el-table__body')]//tr[td[4]/div[contains(@class,'cell') and normalize-space(text())='{account_id.lower()}']]"
    ).first

    try:
        row.wait_for(timeout=50000)
    except PlaywrightTimeoutError:
        logger.exception(f"⚠️ No row matched Username: {account_id}")
        raise Exception(f"<UNK> No row matched Username: {account_id}")

    # 3. Within that row, find and click the "editor" button in the first cell
    editor_btn = row.locator("td:nth-child(1) button", has_text="editor")
    try:
        editor_btn.wait_for(timeout=5_000)
        editor_btn.click()
    except PlaywrightTimeoutError:
        logger.exception(f"❌ No editor button found for Username: {account_id}")
        raise Exception(f"<UNK> No editor button found for Username: {account_id}")

    # 4. Wait for the global "Recharge" button to appear and click it
    try:
        recharge_btn = page.locator("button", has_text="Recharge")
        recharge_btn.wait_for(timeout=5_000, state="visible")
        recharge_btn.click()
    except PlaywrightTimeoutError:
        raise Exception("❌ Timeout while waiting for the Recharge button.")

    logger.debug(f"✅ Clicked recharge button for Username: {account_id}")


def click_redeem_for_account(page: Page, account_id: str, logger):
    logger.debug(f"Searching for redeem button for Username: {account_id}")

    # 1. Wait for at least one row to appear in the table body
    try:
        page.locator("table.el-table__body tr").first.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        logger.warning("⚠️ No table rows found within timeout.")
        raise Exception("<UNK> No table rows found within timeout.")

    # 2. Find the specific <tr> whose 4th <td> .cell text matches account_id
    row = page.locator(
        f"//table[contains(@class,'el-table__body')]//tr[td[4]/div[contains(@class,'cell') and normalize-space(text())='{account_id.lower()}']]"
    ).first

    try:
        row.wait_for(timeout=50000)
    except PlaywrightTimeoutError:
        logger.exception(f"⚠️ No row matched Username: {account_id}")
        raise Exception(f"<UNK> No row matched Username: {account_id}")

    # 3. Within that row, find and click the "editor" button in the first cell
    editor_btn = row.locator("td:nth-child(1) button", has_text="editor")
    try:
        editor_btn.wait_for(timeout=5_000)
        editor_btn.click()
    except PlaywrightTimeoutError:
        logger.exception(f"❌ No editor button found for Username: {account_id}")
        raise Exception(f"<UNK> No editor button found for Username: {account_id}")

    # 4. Wait for the global "Recharge" button to appear and click it
    try:
        redeem_btn = page.locator("button", has_text="Redeem")
        redeem_btn.wait_for(timeout=5_000, state="visible")
        redeem_btn.click()
    except PlaywrightTimeoutError:
        raise Exception("❌ Timeout while waiting for the Redeem button.")

    logger.debug(f"✅ Clicked redeem button for Username: {account_id}")