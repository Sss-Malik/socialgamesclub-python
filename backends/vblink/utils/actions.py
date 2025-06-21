from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import logging

def click_set_score(page: Page, account_id: str, logger: logging.Logger) -> bool:
    logger.debug(f'🔍 Searching for table with "Connect game provider UID" header…')

    # 1) Wait for the table container to appear
    try:
        table = page.locator(
            "div.el-table",
            has=page.locator("th", has_text="Connect game provider UID")
        ).first
        table.wait_for(timeout=10_000)
    except PlaywrightTimeoutError:
        logger.error('❌ Table with "Connect game provider UID" not found within timeout.')
        return False

    # 2) Find the <tr> whose 2nd <td> matches our account_id (case-insensitive)
    logger.debug(f'🔍 Searching for row with account_id="{account_id}"…')
    row = table.locator(
        "tbody tr",
        has=page.locator("td:nth-child(2) .cell", has_text=account_id)
    ).first

    try:
        row.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        logger.error(f'❌ No row found for account_id="{account_id}"')
        return False

    # 3) Within that row, locate and click the "Set Score" button
    btn = row.locator("button", has_text="Set Score")
    try:
        btn.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        logger.error(f'❌ "Set Score" button not found for account_id="{account_id}"')
        return False

    btn.click()
    logger.info(f'✅ Successfully clicked "Set Score" for account_id="{account_id}"')
    return True
