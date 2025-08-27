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


from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def click_account_action(page: Page, account_id: str, logger, action: str):
    logger.debug(f"🔍 Starting pagination scan to locate account: {account_id}")

    action_map = {
        "reset_password": "Reset Password",
        "recharge": "Recharge",
        "withdraw": "Redeem",
        "read": "read"
    }

    if action not in action_map:
        raise ValueError(f"❌ Unknown action: {action}")

    button_text = action_map[action]
    page_num = 1

    while True:
        logger.debug(f"📄 Scanning page {page_num}...")

        try:
            page.locator("table.el-table__body tr").first.wait_for(timeout=15_000)
        except PlaywrightTimeoutError:
            raise Exception("⏱️ Table did not load in time.")

        row_xpath = (
            f"//table[contains(@class,'el-table__body')]"
            f"//tr[td[4]/div[contains(@class,'cell') and normalize-space(text())='{account_id.lower()}']]"
        )
        row = page.locator(row_xpath).first

        try:
            row.wait_for(timeout=20_000)
            logger.debug(f"✅ Found row for account: {account_id}")

            if action == "read":
                return row

            trigger = row.locator("td:nth-child(1) button span", has_text="editor")
            trigger.wait_for(timeout=5_000)
            trigger.click()

            action_item = page.locator(
                "button",
                has_text=button_text
            )
            action_item.wait_for(state="visible", timeout=5_000)
            action_item.click()

            logger.debug(f"✅ Clicked '{button_text}' for Username: {account_id}")
            return row

        except PlaywrightTimeoutError as e:
            logger.debug(f"⚠️ Action flow failed for Username '{account_id}' on page {page_num}: {e}")

        # Try next page
        next_btn = page.locator("button.btn-next:not([disabled])")
        if next_btn.count() > 0:
            logger.debug("➡️ Moving to next page...")
            next_btn.click()
            page.wait_for_timeout(1000)  # Short delay to allow table update
            page_num += 1
        else:
            logger.error(f"🛑 Account '{account_id}' not found after {page_num} pages.")
            raise Exception(f"Account '{account_id}' not found after scanning {page_num} pages.")



