from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

def click_account_action(page: Page, account_id: str, logger, action: str):
    logger.debug(f"Starting pagination scan to locate account: {account_id}")

    action_map = {
        "reset_password": "Reset Password",
        "recharge": "Recharge",
        "withdraw": "Redeem",
        "read": "read"
    }

    if action not in action_map:
        raise ValueError(f"Unknown action: {action}")

    button_text = action_map[action]
    page_num = 1

    while True:
        logger.debug(f"📄 Scanning page {page_num}...")
        try:
            page.locator("table.el-table__body tr").first.wait_for(timeout=50000)
        except PlaywrightTimeoutError:
            raise Exception("No table rows found within timeout.")

        row_xpath = (
            f"//table[contains(@class,'el-table__body')]//tr[td[4]/div[contains(@class,'cell') and normalize-space(text())='{account_id.lower()}']]"
        )
        row = page.locator(row_xpath).first
        try:
            row.wait_for(timeout=10000)
            if action == "read":
                return row
        except PlaywrightTimeoutError:
            logger.debug(f"❌ Username '{account_id}' not found on page {page_num}")
        else:
            trigger = row.locator("td:nth-child(1) span.el-dropdown-link")
            try:
                trigger.wait_for(timeout=5_000)
                trigger.click()
                page.wait_for_timeout(500)
                dropdowns = page.locator("ul.el-dropdown-menu.el-popper")
                for i in range(dropdowns.count()):
                    dropdown = dropdowns.nth(i)
                    if dropdown.is_visible():
                        try:
                            action_item = dropdown.locator("li.el-dropdown-menu__item", has_text=button_text).first
                            action_item.click(force=True)
                            return row
                        except Exception as e:
                            raise Exception(f"[WARN] Dropdown #{i} is visible but Redeem not clickable: {e}")
            except PlaywrightTimeoutError:
                raise Exception(f"No dropdown trigger found for Username: {account_id}")

            # Go to next page if available
        next_btn = page.locator("button.btn-next:not([disabled])")
        if next_btn.count() > 0:
            logger.debug("➡️ Moving to next page...")
            next_btn.click()
            page_num += 1
        else:
            logger.error(f"🛑 Account '{account_id}' not found after {page_num} pages.")
            raise Exception(f"Account '{account_id}' not found after scanning {page_num} pages.")

