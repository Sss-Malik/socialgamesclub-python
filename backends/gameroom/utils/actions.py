from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def click_recharge_for_account(main_frame, account_id: str, logger):
    logger.debug(f"Searching for recharge button for Username: {account_id}")

    # wait for the table container to be visible
    main_frame.locator("div.layui-table-box").wait_for(state="visible", timeout=5_000)

    # locate the <tr> whose 4th <td> contains our account_id
    row = main_frame.locator(
        "div.layui-table-box table.layui-table tbody tr",
        has=main_frame.locator("td:nth-child(4) div.layui-table-cell", has_text=account_id)
    ).first

    # ensure the row exists
    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"No table row found for account ID: {account_id}")

    # find and click the "Recharge" button in the 11th cell
    button = row.locator('td:nth-child(11) button', has_text="Recharge")
    try:
        button.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"Recharge button not found in row for account ID: {account_id}")

    button.click()
    logger.info(f"✅ Clicked recharge button for Username: {account_id}")
