from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def click_recharge_for_account(main_frame, account_id: str, logger):
    logger.debug(f"Searching for recharge button for Username: {account_id}")

    # wait for the table container to be visible
    main_frame.locator("div.layui-table-box").wait_for(state="visible", timeout=5_000)

    # locate the <tr> whose 4th <td> contains our account_id
    row = main_frame.locator(
        f"//div[contains(@class,'layui-table-box')]//table[contains(@class,'layui-table')]//tbody//tr[td[4]/div[contains(@class,'layui-table-cell') and normalize-space(text())='{account_id}']]"
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
    logger.debug(f"✅ Clicked recharge button for Username: {account_id}")


def click_withdraw_for_account(main_frame, account_id: str, logger):
    logger.debug(f"Searching for withdraw button for Username: {account_id}")

    # wait for the table container to be visible
    main_frame.locator("div.layui-table-box").wait_for(state="visible", timeout=5_000)

    # locate the <tr> whose 4th <td> contains our account_id
    row = main_frame.locator(
        f"//div[contains(@class,'layui-table-box')]//table[contains(@class,'layui-table')]//tbody//tr[td[4]/div[contains(@class,'layui-table-cell') and normalize-space(text())='{account_id}']]"
    ).first

    # ensure the row exists
    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"No table row found for account ID: {account_id}")

    # find and click the "Recharge" button in the 11th cell
    button = row.locator('td:nth-child(11) button', has_text="Withdraw")
    try:
        button.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        raise Exception(f"Withdraw button not found in row for account ID: {account_id}")

    button.click()
    logger.debug(f"✅ Clicked withdraw button for Username: {account_id}")

