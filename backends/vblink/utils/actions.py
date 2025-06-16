from playwright.sync_api import Page, TimeoutError

def click_set_score(page: Page, account_id: str, logger) -> bool:
    """
    In the Element-UI table whose header has "Connect game provider UID",
    find the row whose User Name column equals account_id, and click its
    "Set Score" button.
    """
    try:
        # 1. Find the Element-UI table container by its header
        table_container = page.locator(
            "div.el-table",
            has=page.locator('th:has-text("Connect game provider UID")')
        ).first
        if table_container.count() == 0:
            logger.error('No Element-UI table found with header "Connect game provider UID"')
            return False

        # 2. Build an XPath that:
        #    - picks the <tr> whose 2nd <td> exactly matches account_id
        #    - then selects the Set Score <button>
        xpath = (
            f".//tbody/tr[td[2][normalize-space()='{account_id.lower()}']]"
            "//button[.//span[text()='Set Score']]"
        )

        button_locator = table_container.locator(f"xpath={xpath}").first
        if button_locator.count() == 0:
            logger.error(f'"Set Score" button not found for User Name="{account_id.lower()}"')
            return False

        # 3. Click it
        button_locator.click()
        logger.info(f'Successfully clicked "Set Score" for "{account_id}"')
        return True

    except TimeoutError as te:
        logger.error(f'Timeout waiting for elements: {te}')
        return False
    except Exception as e:
        logger.exception(f'Unexpected error in click_set_score: {e}')
        return False
