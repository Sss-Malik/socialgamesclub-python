def inject_session_token(page, token, expires_time, login_url):
    page.evaluate("""
        (token) => {
            sessionStorage.setItem('token', token);
        }
    """, token)

    page.evaluate("""
            (expires) => {
                sessionStorage.setItem('expires_time', expires);
            }
        """, expires_time)
    page.goto(login_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)



from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def validate_session_token(page, logger) -> bool | None:
    try:
        timeout_modal = page.locator("div#layerTimeout")
        timeout_modal.wait_for(state="visible", timeout=5000)
        page.locator("a.layui-layer-btn0").click()
        return False
    except PlaywrightTimeoutError:
        return True
