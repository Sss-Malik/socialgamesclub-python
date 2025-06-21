from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def click_update_for_account(main_frame, account_id: str, logger):
    logger.debug(f"Searching for update button for account ID: {account_id}")

    try:
        main_frame.locator("#item tr.list").first.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"⚠️ No table rows found within timeout.")

    # Locate the specific <tr> whose 3rd <td> matches our account_id
    row = main_frame.locator(
        "#item tr.list",
        has=main_frame.locator("td:nth-child(3)", has_text=account_id)
    ).first

    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"⚠️ No matching row found for account ID: {account_id}")

    # Within that row, find the <a> in the first cell and click it
    update_btn = row.locator("td:nth-child(1) a")
    try:
        update_btn.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"❌ Update button not found in row for account ID: {account_id}")

    update_btn.click()
    logger.info(f"✅ Clicked update button for account ID: {account_id}")
