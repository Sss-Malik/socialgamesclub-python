from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def click_update_for_account(main_frame, account_id: str, logger):
    logger.debug(f"Searching for update button for account ID: {account_id}")

    # Build a locator for the <tr> whose 3rd <td> text matches account_id
    row = main_frame.locator(
        f"//tr[contains(@class, 'list')][td[3][normalize-space(text())='{account_id}']]"
    ).first

    # Wait for that row to appear
    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"No table row found for account ID: {account_id}")

    # Within that row, find the <a> in the first <td> and click it
    update_btn = row.locator("td:nth-child(1) a")
    try:
        update_btn.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"Update button not found in row for account ID: {account_id}")

    update_btn.click()
    logger.info(f"✅ Clicked update button for account ID: {account_id}")
