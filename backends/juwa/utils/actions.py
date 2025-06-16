from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def click_recharge_for_account(page, account_id, logger):
    try:
        max_wait = 10
        found = False

        # Wait up to max_wait seconds for table rows to appear
        for _ in range(max_wait):
            rows = page.query_selector_all("table.el-table__body tr")
            if rows:
                found = True
                break
            page.wait_for_timeout(1000)

        if not found:
            logger.warning("⚠️ No table rows found within timeout.")
            return

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 12:
                continue

            username_cell = cells[3].query_selector(".cell")
            if not username_cell:
                continue

            username = username_cell.inner_text().strip()
            if username == account_id:
                # Find the dropdown trigger span inside the first cell
                dropdown_trigger = cells[0].query_selector("div.el-dropdown > span.el-dropdown-link")
                if not dropdown_trigger:
                    logger.error(f"❌ No dropdown trigger found for Username: {account_id}")
                    return

                # Hover or click the dropdown to reveal menu
                dropdown_trigger.hover()
                page.wait_for_timeout(1000)

                # The dropdown menu should now be visible somewhere in the DOM (not necessarily inside the row)
                # Locate the visible menu item with text "Recharge"
                menu_item = page.query_selector("ul.el-dropdown-menu.el-popper li.el-dropdown-menu__item:visible:text('Recharge')")
                if not menu_item:
                    # If :visible:text() selector does not work, fallback to querying visible + filtering manually:
                    menu_items = page.query_selector_all("ul.el-dropdown-menu.el-popper li.el-dropdown-menu__item")
                    menu_item = None
                    for item in menu_items:
                        if item.is_visible() and item.inner_text().strip() == "Recharge":
                            menu_item = item
                            break

                if menu_item:
                    menu_item.click()
                    logger.info(f"✅ Clicked 'Recharge' from dropdown for Username: {account_id}")
                    return
                else:
                    logger.error(f"❌ 'Recharge' menu item not found or not visible for Username: {account_id}")
                    return

        logger.warning(f"⚠️ No row matched Username: {account_id}")

    except PlaywrightTimeoutError:
        logger.exception("⏳ Timeout while trying to click recharge.")
    except Exception as e:
        logger.exception(f"❌ Error clicking recharge for Username {account_id}: {e}")
