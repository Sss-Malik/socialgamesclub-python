from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time

def click_purchase_for_account(page, account_id, logger):
    try:
        max_wait = 10
        found = False

        for _ in range(max_wait):
            rows = page.query_selector_all("#table-accounts tbody tr[rel='account']")
            if rows:
                found = True
                break
            time.sleep(1)

        if not found:
            logger.warning("⚠️ No account rows found within timeout.")
            return

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 8:
                continue  # Skip incomplete rows

            username_cell = cells[3]
            username = username_cell.inner_text().strip()

            if username == account_id:
                redeem_btn = cells[7].query_selector("button:has-text('Purchase')")
                if redeem_btn:
                    redeem_btn.click()
                    logger.info(f"✅ Clicked Purchase button for Username: {account_id}")
                    return
                else:
                    logger.error(f"❌ Purchase button not found for Username: {account_id}")
                    return

        logger.warning(f"⚠️ No row matched Username: {account_id}")

    except PlaywrightTimeoutError:
        logger.exception("⏳ Timeout while trying to click Purchase.")
    except Exception as e:
        logger.exception(f"❌ Error clicking Redeem for Username {account_id}: {e}")
