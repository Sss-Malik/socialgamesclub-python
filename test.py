from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

_play = sync_playwright().start()
BROWSER = _play.chromium.launch(
    headless=False,
    args=[
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
)

context = BROWSER.new_context()
page = context.new_page()
page.goto("https://gm.vblink777.club/#/index")

username = "TestVB159"
password = "test12345"

page.locator('input[name="userName"]').fill(username)
page.locator('input[name="passWd"]').fill(password)

page.locator('button:has-text("Login")').click()
page.locator('section.app-main').wait_for(timeout=20_000)
print("login successful")
page.reload(wait_until="domcontentloaded")

try:
    # Locate the dialog by ARIA role and title text
    dialog = page.get_by_role(
        "dialog",
        name="Hint"
    )

    # Wait briefly for dialog to appear
    dialog.wait_for(state="visible", timeout=5000)

    # Ensure the dialog contains the expected warning text
    dialog.locator(
        "text=To ensure the security of your account"
    ).wait_for(timeout=3000)

    # Click the Confirm button inside the dialog
    dialog.get_by_role("button", name="confirm").click()

    print("google authenticator bind dialog detected and closed")

except PlaywrightTimeoutError:
    # Dialog did not appear — safe to continue
    print("google authenticator bind dialog not present, continuing.")

token = page.evaluate("() => sessionStorage.getItem('Admin-Token')")