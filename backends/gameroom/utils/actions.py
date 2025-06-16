from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def click_recharge_for_account(main_frame, account_id, logger):
    try:
        # Wait for the table to appear
        main_frame.wait_for_selector("table.layui-table", timeout=5000, state="visible")

        # Select all data rows in the table
        rows = main_frame.query_selector_all("table.layui-table tbody tr")

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue  # Skip malformed rows

            # Extract the username from the 3rd column (index 2)
            username_div = cells[3].query_selector("div.layui-table-cell")
            if not username_div:
                continue

            username = username_div.inner_text().strip()
            if username == account_id:
                # Or try clicking a button/link if it exists
                button = cells[10].query_selector('button:has-text("Recharge")')
                if button:
                    button.click()
                    logger.info(f"✅ Clicked recharge button for Username: {account_id}")
                    return

                logger.error(f"❌ No clickable element found in row for Username: {account_id}")
                return

        logger.warning(f"⚠️ No table row matched Username: {account_id}")

    except PlaywrightTimeoutError:
        logger.exception("⏳ Timeout while trying to click recharge.")
    except Exception as e:
        logger.exception(f"❌ Error clicking recharge for Username {account_id}: {e}")
