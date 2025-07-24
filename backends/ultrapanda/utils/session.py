from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def inject_session_token(page, token):
    page.evaluate("""
        (token) => {
            sessionStorage.setItem('Admin-Token', token);
        }
    """, token)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(2000)



def validate_session_token(page, logger) -> bool | None:
    try:
        page.locator("p.el-message__content").first.wait_for(state="attached", timeout=1000)
        messages = page.locator("p.el-message__content").all()
        for message in messages:
            text = message.inner_text().strip().lower()
            if "error: 52, no restriciton" in text or "您的账号已在其它设备登录" in text:
                page.wait_for_timeout(2000)
                return False

        timeout_box = page.locator("div.el-message-box__message p")
        timeout_box.wait_for(state="visible", timeout=2000)
        text = timeout_box.inner_text().strip().lower()
        if "browser timeout" in text or "log in again" in text:
            return False
        return None
    except PlaywrightTimeoutError:
        return True
