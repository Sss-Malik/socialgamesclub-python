# main.py

import random
import string
import sys
import io
import time
from pathlib import Path
from PIL import Image, ImageOps
import pytesseract
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# === Configuration ===
LOGIN_URL   = "https://orionstars.vip:8781/default.aspx"
USERNAME    = "TestOS159"
PASSWORD    = "Test@159872!!"
OUTPUT_FILE = Path("created_players.txt")

def solve_captcha(page, timeout: int = 10000) -> str:
    """
    1. Wait for <img id="ImageCheck"> to appear.
    2. Take an element screenshot (PNG bytes).
    3. Enlarge by 3×, convert to grayscale, then threshold at 128 (mode='1').
    4. Save both raw/thresholded images for debugging (folder: current working dir).
    5. Run Tesseract OCR (digits-only, PSM 8). Extract exactly 5 digits. Raise ValueError otherwise.
    """
    # 1) Wait for the captcha image element
    img_el = page.wait_for_selector("img#ImageCheck", timeout=timeout)
    # 2) Screenshot that element (returns PNG bytes)
    img_bytes = img_el.screenshot(type="png")

    # 3) Save raw screenshot for debugging
    ts = int(time.time())
    raw_filename = f"captcha_raw_{ts}.png"
    with open(raw_filename, "wb") as f_raw:
        f_raw.write(img_bytes)
    print(f"🔍 Saved raw captcha to {raw_filename}")

    # 4) Load into PIL, enlarge by 3×, convert to grayscale, threshold at 128
    pil_img = Image.open(io.BytesIO(img_bytes))
    w, h = pil_img.size
    # Enlarge by 3× using LANCZOS (high‐quality downscale/upscale filter)
    big_img = pil_img.resize((w * 3, h * 3), Image.LANCZOS)

    gray = ImageOps.grayscale(big_img)       # 8-bit grayscale
    bw   = gray.point(lambda x: 0 if x < 128 else 255, mode="1")  # B/W threshold

    # 5) Save thresholded image for debugging
    bw_filename = f"captcha_bw_{ts}.png"
    bw.save(bw_filename)
    print(f"🔍 Saved thresholded captcha to {bw_filename}")

    # 6) Run Tesseract OCR with digits-only whitelist, PSM 8 (“treat as single word”)
    #    PSM 8 proved reliable on the debug image once resized+thresholded.
    bw_l = bw.convert("L")  # Tesseract expects ‘L’ or ‘RGB’, not mode=‘1’
    custom_config = r"--psm 8 -c tessedit_char_whitelist=0123456789"
    raw_text = pytesseract.image_to_string(bw_l, config=custom_config)

    # 7) Keep only digits from the OCR result
    digits = "".join(ch for ch in raw_text if ch.isdigit())

    # 8) If it’s not exactly 5 digits, something is wrong
    if len(digits) != 5:
        raise ValueError(f"Captcha OCR returned '{digits}' (expected 5 digits).")
    return digits

def solve_captcha_with_retries(page, tries: int = 3, timeout: int = 10000) -> str:
    """
    Attempt solve_captcha() up to `tries` times. If OCR fails (ValueError or empty),
    click the captcha image to refresh it, wait 1 second, then retry. If still failing
    after `tries`, propagate the last exception.
    """
    for attempt in range(1, tries + 1):
        try:
            result = solve_captcha(page, timeout=timeout)
            return result
        except Exception as err:
            print(f"[Attempt {attempt}/{tries}] OCR failed: {err}")
            if attempt == tries:
                # On the last attempt, give up and re-raise
                raise
            # Otherwise, click the captcha image to get a new code
            page.click("img#ImageCheck")  # onclick="ChangeCodeimg();" is bound to this element
            page.wait_for_timeout(1000)   # give the new image ~1 second to load
    # We should never reach here, because last attempt either returns or raises
    raise RuntimeError("solve_captcha_with_retries: fell through all attempts unexpectedly.")


def generate_credentials():
    """
    Return (username, password), where:
      - username: random 8–13 chars from [A-Za-z0-9_]
      - password: random 10–16 chars from [A-Za-z0-9_]
    """
    letters_digits = string.ascii_letters + string.digits + "_"
    username = "".join(random.choices(letters_digits, k=random.randint(8, 13)))
    password = "".join(random.choices(letters_digits, k=random.randint(10, 16)))
    return username, password


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            # === Step 1: Open the login page ===
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            print("Opened login page.")

            # === Step 2: Solve & fill the Captcha ===
            page.wait_for_selector("img#ImageCheck", timeout=10_000)
            captcha_code = solve_captcha_with_retries(page, tries=3, timeout=10_000)
            print(f"🔒 Captcha recognized as: {captcha_code}")
            page.fill("#txtVerifyCode", captcha_code)

            # === Step 3: Fill login credentials and submit ===
            page.wait_for_selector("#txtLoginName", timeout=10_000)
            page.fill("#txtLoginName", USERNAME)
            page.fill("#txtLoginPass", PASSWORD)
            page.wait_for_timeout(1500)   # a brief pause, similar to your original sleeps
            page.click("#btnLogin")
            print("Submitted login form.")
            page.wait_for_selector("iframe#frm_left_frm", timeout=20_000)

            # === Step 4: Switch to the left‐nav iframe (“User Management”) ===
            left_iframe_el = page.query_selector("iframe#frm_left_frm")
            if not left_iframe_el:
                raise Exception("Cannot find <iframe id='frm_left_frm'>.")
            left_frame = left_iframe_el.content_frame()
            if not left_frame:
                raise Exception("Could not obtain content_frame() for left iframe.")

            left_frame.wait_for_selector("xpath=//span[contains(text(),'User Management')]", timeout=10_000)
            left_frame.click("xpath=//span[contains(text(),'User Management')]")
            print("Clicked 'User Management'.")
            page.wait_for_timeout(1000)

            # === Step 5: Switch to the main‐content iframe (“Create Player”) ===
            page.wait_for_selector("iframe#frm_main_content", timeout=10_000)
            main_iframe_el = page.query_selector("iframe#frm_main_content")
            if not main_iframe_el:
                raise Exception("Cannot find <iframe id='frm_main_content'>.")
            main_frame = main_iframe_el.content_frame()
            if not main_frame:
                raise Exception("Could not obtain content_frame() for main iframe.")

            print("Switched to main content.")
            main_frame.wait_for_selector("xpath=//a[contains(text(),'Create Player')]", timeout=10_000)
            main_frame.click("xpath=//a[contains(text(),'Create Player')]")
            print("Clicked 'Create Player' button.")
            page.wait_for_timeout(2000)

            # === Step 6: Switch to the CreateAccount dialog iframe ===
            iframe_dialog_el = page.wait_for_selector(
                "xpath=//iframe[contains(@src,'CreateAccount.aspx')]", timeout=15_000
            )
            dialog_frame = iframe_dialog_el.content_frame()
            if not dialog_frame:
                raise Exception("Failed to switch to Create Player iframe.")
            print("Switched to Create Player iframe.")

            # === Step 7: Fill out and submit the “Create Player” form ===
            new_username, new_password = generate_credentials()
            dialog_frame.wait_for_selector("#txtAccount", timeout=10_000)
            dialog_frame.fill("#txtAccount", new_username)
            dialog_frame.wait_for_timeout(1000)
            dialog_frame.fill("#txtLogonPass", new_password)
            dialog_frame.wait_for_timeout(500)
            dialog_frame.fill("#txtLogonPass2", new_password)
            dialog_frame.wait_for_timeout(1500)

            # === Step 8: Click the final “Create Player” button ===
            final_btn = dialog_frame.locator(
                "xpath=//a[contains(@class,'btn13') and contains(text(),'Create Player')]"
            )
            final_btn.wait_for(state="visible", timeout=10_000)
            final_btn.click()
            print("Submitted Create Player form.")
            page.wait_for_timeout(3000)

            # === Step 9: Save credentials to file ===
            with OUTPUT_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{new_username}:{new_password}\n")
            print(f"✅ Created player saved: {new_username}:{new_password}")

        except PlaywrightTimeoutError as te:
            print(f"TimeoutError: {te}", file=sys.stderr)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        finally:
            print("Script completed. Browser remains open (headed mode).")
            # If you prefer the browser to close automatically, uncomment:
            # browser.close()

if __name__ == "__main__":
    main()
