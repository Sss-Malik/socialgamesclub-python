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
    msg_locator = page.locator("p.el-message__content")

    if msg_locator.count() > 0:
        for item in msg_locator.all():
            text = item.inner_text().strip().lower()
            if "error: 52, no restriciton" in text or "您的账号已在其它设备登录" in text:
                logger.debug("Session invalid warning detected")
                return False

    timeout_box = page.locator("div.el-message-box__message p")
    if timeout_box.is_visible(timeout=2000):
        text = timeout_box.inner_text().strip().lower()
        if "browser timeout" in text or "log in again" in text:
            logger.debug("Session invalid warning detected")
            return False

    return True

