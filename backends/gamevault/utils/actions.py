from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time

def click_recharge_for_account(page, account_id, logger):
    try:
        # Wait manually for up to 10s for rows to appear
        max_wait = 10
        found = False
        for _ in range(max_wait):
            rows = page.query_selector_all("table.el-table__body tr")
            if rows:
                found = True
                break
            time.sleep(1)

        if not found:
            logger.warning("⚠️ No table rows found within timeout.")
            return

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 12:
                continue

            # Username is in 4th column (index 3)
            username_cell = cells[3].query_selector(".cell")
            if not username_cell:
                continue

            username = username_cell.inner_text().strip()
            if username == account_id:
                # Click the editor button in the first cell
                editor_btn = cells[0].query_selector("button:has-text('editor')")
                if editor_btn:
                    editor_btn.click()
                    recharge_btn = page.wait_for_selector("button:has-text('Recharge')", timeout=5000, state="visible")
                    if recharge_btn:
                        recharge_btn.click()
                    logger.info(f"✅ Clicked recharge button for Username: {account_id}")
                    return

                logger.error(f"❌ No editor button found for Username: {account_id}")
                return

        logger.warning(f"⚠️ No row matched Username: {account_id}")

    except PlaywrightTimeoutError:
        logger.exception("⏳ Timeout while trying to click recharge.")
    except Exception as e:
        logger.exception(f"❌ Error clicking recharge for Username {account_id}: {e}")