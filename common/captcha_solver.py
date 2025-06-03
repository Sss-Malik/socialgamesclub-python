# common/captcha_solver.py

import io
import time
from pathlib import Path
from PIL import Image, ImageOps
import pytesseract
from playwright.sync_api import Page

def solve_captcha(page: Page, save_dir: Path, timeout: int = 10000) -> str:
    """
    1. Wait for <img id="ImageCheck"> to appear.
    2. Take an element screenshot (PNG bytes).
    3. Enlarge by 3×, convert to grayscale, then threshold at 128 (mode='1').
    4. Save raw+thresholded images under save_dir (for debugging).
    5. Run Tesseract OCR (digits-only, PSM 8). Extract exactly 5 digits. Raise ValueError otherwise.
    """
    # 1) Wait for the captcha image element
    img_el = page.wait_for_selector("img#ImageCheck", timeout=timeout)
    # 2) Screenshot that element (returns PNG bytes)
    img_bytes = img_el.screenshot(type="png")

    # 3) Save raw screenshot for debugging
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    raw_filename = save_dir / f"captcha_raw_{ts}.png"
    with open(raw_filename, "wb") as f_raw:
        f_raw.write(img_bytes)

    # 4) Load into PIL, enlarge by 3×, convert to grayscale, threshold at 128
    pil_img = Image.open(io.BytesIO(img_bytes))
    w, h = pil_img.size
    big_img = pil_img.resize((w * 3, h * 3), Image.LANCZOS)
    gray = ImageOps.grayscale(big_img)
    bw = gray.point(lambda x: 0 if x < 128 else 255, mode="1")

    # 5) Save thresholded image
    bw_filename = save_dir / f"captcha_bw_{ts}.png"
    bw.save(bw_filename)

    # 6) Run Tesseract OCR with digits-only whitelist, PSM 8
    bw_l = bw.convert("L")
    custom_config = r"--psm 8 -c tessedit_char_whitelist=0123456789"
    raw_text = pytesseract.image_to_string(bw_l, config=custom_config)

    # 7) Keep only digits from the OCR result
    digits = "".join(ch for ch in raw_text if ch.isdigit())

    # 8) If it’s not exactly 5 digits, error
    if len(digits) != 5:
        raise ValueError(f"Captcha OCR returned '{digits}' (expected 5 digits).")
    return digits

def solve_captcha_with_retries(page: Page, save_dir: Path, tries: int = 3, timeout: int = 10000) -> str:
    """
    Attempt solve_captcha() up to `tries` times. If OCR fails (ValueError or empty),
    click the captcha image to refresh it, wait 1 second, then retry. If still failing
    after `tries`, propagate the last exception.
    """
    for attempt in range(1, tries + 1):
        try:
            result = solve_captcha(page, save_dir=save_dir, timeout=timeout)
            return result
        except Exception as err:
            print(f"[Attempt {attempt}/{tries}] OCR failed: {err}")
            if attempt == tries:
                raise
            page.click("img#ImageCheck")  # triggers ChangeCodeimg()
            page.wait_for_timeout(1000)
    # Should never reach here
    raise RuntimeError("solve_captcha_with_retries: fell through all attempts unexpectedly.")
