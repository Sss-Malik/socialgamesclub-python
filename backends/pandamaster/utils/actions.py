from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def click_update_for_account(main_frame, account_id, logger):
    try:
        # Wait for table to appear
        main_frame.wait_for_selector("#item", timeout=5000)

        # Select all rows except the header
        rows = main_frame.query_selector_all("#item tr.list")

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue  # skip if row structure is unexpected

            account_cell_text = cells[2].inner_text().strip()

            if account_cell_text == account_id:
                update_btn = cells[0].query_selector("a")
                if update_btn:
                    update_btn.click()
                    logger.info(f"✅ Clicked update button for account ID: {account_id}")
                    return
                else:
                    logger.error(f"❌ Update button not found in row for account ID: {account_id}")
                    return

        logger.warning(f"⚠️ No row found for account ID: {account_id}")

    except PlaywrightTimeoutError:
        logger.exception("⏳ Timeout while trying to click update button.")
    except Exception as e:
        logger.exception(f"❌ Error clicking update button for account ID {account_id}: {e}")