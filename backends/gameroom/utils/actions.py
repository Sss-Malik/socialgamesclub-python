from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def click_account_action(
    main_frame,
    username: str,
    action: str,                      # "recharge" | "withdraw" | "reset_password"
    logger,
    *,
    first_paint_timeout: int = 50_000,
    per_page_timeout: int = 6_000,
    page_scan_limit: int = 200
):

    logger.debug(f"Searching for '{action}' button for Username: {username}")

    # ---- ensure table is present/rendered ----
    table_box = main_frame.locator("div.layui-table-box")
    table_box.wait_for(state="visible", timeout=first_paint_timeout)

    main_body = main_frame.locator("div.layui-table-body.layui-table-main")
    # Wait for any row or accept that table may be empty (we'll handle not found gracefully)
    try:
        main_body.locator("tbody tr").first.wait_for(state="visible", timeout=per_page_timeout)
    except PlaywrightTimeoutError:
        raise Exception("Timeout while waiting for table rows")

    # ---- page helpers ----
    pager_root = main_frame.locator("div.layui-laypage")

    def find_row_on_current_page():
        main_frame.wait_for_timeout(5000)
        r = main_body.locator(
            f"//tr[td[@data-field='Account']/div[normalize-space()='{username}']]"
        ).first
        return r if r.count() else None

    def click_action_in_row(r):
        # Button lives in Operation cell (data-field='10'), anchor has lay-event
        btn = r.locator(f"td[data-field='10'] a[lay-event='{action}'] button")
        btn.wait_for(state="visible", timeout=per_page_timeout)
        r.scroll_into_view_if_needed()
        btn.click()
        logger.debug(f"✅ Clicked '{action}' for Username: {username}")

    def go_to_next_page():
        next_btn = pager_root.locator("a.layui-laypage-next:not(.layui-disabled)")
        if not next_btn.count():
            return False
        next_btn.click()
        try:
            main_body.locator("tbody tr").first.wait_for(state="visible", timeout=per_page_timeout)
        except PlaywrightTimeoutError:
           raise Exception("Timeout while waiting for table rows")
        return True

    # ---- search current page, then paginate ----
    pages_checked = 0
    while True:
        pages_checked += 1
        row = find_row_on_current_page()
        if row:
            if action == "read":
                return row
            click_action_in_row(row)
            return None

        # If no pager, or we've hit a sensible limit, stop.
        if not pager_root.count() or pages_checked >= page_scan_limit:
            break

        # Try next page; if there's no next, we're done.
        if not go_to_next_page():
            break

    raise Exception(f"Username '{username}' not found for action '{action}' after scanning {pages_checked} page(s).")
