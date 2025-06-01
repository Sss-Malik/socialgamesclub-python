# main.py

import random
import string
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# === Configuration ===
LOGIN_URL = "https://orionstars.vip:8781/default.aspx"
USERNAME = "TestOS159"
PASSWORD = "Test@159872!!"
OUTPUT_FILE = Path("created_players.txt")

def generate_credentials():
    """
    Generate a random username (8–13 chars) and password (10–16 chars),
    using letters, digits, and underscore. Returns a tuple (username, password).
    """
    letters_digits = string.ascii_letters + string.digits + "_"
    username = "".join(random.choices(letters_digits, k=random.randint(8, 13)))
    password = "".join(random.choices(letters_digits, k=random.randint(10, 16)))
    return username, password

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            # === Step 1: Open login page ===
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            print("Opened login page.")
            page.wait_for_selector("#txtLoginName", timeout=10_000)

            # === Step 2: Enter login credentials ===
            page.fill("#txtLoginName", USERNAME)
            page.fill("#txtLoginPass", PASSWORD)
            page.wait_for_timeout(8000)
            page.click("#btnLogin")
            print("Submitted login form.")
            page.wait_for_selector("iframe#frm_left_frm", timeout=20_000)

            # === Step 3: Go to "User Management" ===
            left_iframe_el = page.query_selector("iframe#frm_left_frm")
            if not left_iframe_el:
                raise Exception("Cannot find <iframe id='frm_left_frm'>")
            left_frame = left_iframe_el.content_frame()
            if not left_frame:
                raise Exception("Could not obtain content_frame() for left iframe.")

            left_frame.wait_for_selector("xpath=//span[contains(text(), 'User Management')]", timeout=10_000)
            left_frame.click("xpath=//span[contains(text(), 'User Management')]")
            print("Clicked 'User Management'.")
            page.wait_for_timeout(1000)

            # === Step 4: Switch to main content frame ===
            page.wait_for_selector("iframe#frm_main_content", timeout=10_000)
            main_iframe_el = page.query_selector("iframe#frm_main_content")
            if not main_iframe_el:
                raise Exception("Cannot find <iframe id='frm_main_content'>")
            main_frame = main_iframe_el.content_frame()
            if not main_frame:
                raise Exception("Could not obtain content_frame() for main iframe.")

            print("Switched to main content.")
            main_frame.wait_for_selector("xpath=//a[contains(text(), 'Create Player')]", timeout=10_000)

            # === Step 5: Click "Create Player" ===
            main_frame.click("xpath=//a[contains(text(), 'Create Player')]")
            print("Clicked 'Create Player' button.")
            page.wait_for_timeout(5000)

            # === Step 6: Switch to dialog iframe ===
            # Wait for the CreateAccount iframe (src contains 'CreateAccount.aspx'):
            iframe_dialog_el = page.wait_for_selector(
                "xpath=//iframe[contains(@src, 'CreateAccount.aspx')]",
                timeout=15_000
            )
            # Since wait_for_selector returned an ElementHandle, call content_frame() directly:
            dialog_frame = iframe_dialog_el.content_frame()
            if not dialog_frame:
                raise Exception("Failed to switch to Create Player iframe.")
            print("Switched to Create Player iframe.")

            # === Step 7: Fill out and submit form ===
            new_username, new_password = generate_credentials()
            dialog_frame.wait_for_selector("#txtAccount", timeout=10_000)
            dialog_frame.fill("#txtAccount", new_username)
            dialog_frame.wait_for_timeout(1000)
            dialog_frame.fill("#txtLogonPass", new_password)
            dialog_frame.wait_for_timeout(500)
            dialog_frame.fill("#txtLogonPass2", new_password)
            dialog_frame.wait_for_timeout(1500)

            # === Step 8: Click "Create Player" final button ===
            final_btn = dialog_frame.locator(
                "xpath=//a[contains(@class, 'btn13') and contains(text(), 'Create Player')]"
            )
            final_btn.wait_for(state="visible", timeout=10_000)
            final_btn.click()
            print("Submitted Create Player form.")
            page.wait_for_timeout(3000)

            # === Step 9: Save credentials ===
            with OUTPUT_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{new_username}:{new_password}\n")
            print(f"✅ Created player saved: {new_username}:{new_password}")

        except PlaywrightTimeoutError as te:
            print(f"TimeoutError: {te}", file=sys.stderr)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        finally:
            print("Script completed. Browser remains open (headed mode).")
            # To close automatically instead, uncomment:
            # browser.close()

if __name__ == "__main__":
    main()
