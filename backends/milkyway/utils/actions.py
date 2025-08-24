import re
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Frame

TD_ACCOUNT_COL = "td:nth-child(3)"
ROW_SELECTOR   = "tr.list"
UPDATE_BTN_SEL = "td:nth-child(1) a[onclick^='updateSelect']"
TABLE_SEL      = "#item"
PAGER_SEL      = "#anpPage"
NEXT_LINK_SEL  = "#anpPage a:has-text('Next')"

def _row_locator_for_account(frame: Frame, account_id: str):
    # exact match with whitespace tolerance
    exact = re.compile(rf"^\s*{re.escape(account_id)}\s*$")
    return frame.locator(ROW_SELECTOR).filter(
        has=frame.locator(TD_ACCOUNT_COL).filter(has_text=exact)
    )

def _pager_present(frame: Frame) -> bool:
    try:
        return frame.locator(PAGER_SEL).first.is_visible()
    except:
        return False

def _pager_has_next(frame: Frame) -> bool:
    next_link = frame.locator(NEXT_LINK_SEL)
    if not next_link.count():
        return False
    return next_link.get_attribute("disabled") is None

def _click_next_and_wait(frame: Frame, timeout_ms: int = 10000):
    table = frame.locator(TABLE_SEL)
    # safer to read via evaluate so we don't get trimmed text
    old_html = table.evaluate("el => el.innerHTML")
    frame.locator(NEXT_LINK_SEL).first.click()
    frame.wait_for_function(
        "({ old, sel }) => {"
        "  const el = document.querySelector(sel);"
        "  return !!el && el.innerHTML !== old;"
        "}",
        arg={"old": old_html, "sel": TABLE_SEL},
        timeout=timeout_ms,
    )

def click_update_for_account(
    main_frame: Frame,
    account_id: str,
    logger,
    *,
    per_page_wait_ms: int = 1500,
    timeout_ms: int = 5000,
    max_pages: int = 100
) -> None:
    """
    Clicks Update for the row where Account == account_id.
    Works with or without a pager. Scans all pages if pager+Next exist.
    """
    # Ensure table exists
    try:
        main_frame.locator(TABLE_SEL).wait_for(timeout=timeout_ms)
    except PlaywrightTimeoutError:
        raise Exception("Accounts table (#item) did not render.")

    def _try_click_on_current_page() -> bool:
        row = _row_locator_for_account(main_frame, account_id).first
        try:
            row.wait_for(timeout=per_page_wait_ms)
        except PlaywrightTimeoutError:
            return False
        btn = row.locator(UPDATE_BTN_SEL)
        try:
            btn.wait_for(timeout=timeout_ms)
        except PlaywrightTimeoutError:
            raise Exception(f"Update button not found in row for account '{account_id}'.")
        btn.click()
        logger.debug(f"🟢 Clicked Update for account '{account_id}'.")
        return True

    # Fast path: single page (no pager) OR present but item is on first page
    if _try_click_on_current_page():
        return

    if not _pager_present(main_frame):
        # No pager at all → nothing else to scan
        raise Exception(f"Account '{account_id}' not found on the page (no pagination present).")

    # Pager exists → iterate pages via Next
    scanned_pages = 1
    while scanned_pages < max_pages and _pager_has_next(main_frame):
        scanned_pages += 1
        logger.debug(f"📄 Scanning page {scanned_pages}…")
        try:
            _click_next_and_wait(main_frame, timeout_ms=10000)
        except PlaywrightTimeoutError:
            raise Exception("Clicked Next but table did not update in time.")

        if _try_click_on_current_page():
            return

    raise Exception(
        f"Account '{account_id}' not found after scanning {scanned_pages} page(s). "
        "Verify the ID or increase max_pages."
    )
