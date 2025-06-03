from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Per‐backend config & paths:
from backends.orionstars.config import BACKEND_NAME, LOGIN_URL, USERNAME, PASSWORD, DATA_DIR, LOGS_DIR

# Common utilities:
from common.logger import get_backend_logger
from common.captcha_solver import solve_captcha_with_retries
from common.credential_utils import generate_credentials

def run():
    """
    Main entrypoint for orionstars backend automation.
    1. Open the login page
    2. Solve & fill the captcha
    3. Fill login form
    4. Click “User Management”
    5. Click “Create Player”
    6. Fill new player form & submit
    7. Save credentials to file
    8. Log each step in logs/automation.log
    """
    # Ensure our data/log directories exist
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    LOGS_DIR.mkdir(exist_ok=True, parents=True)

    # Set up logger (writes to both console + logs/automation.log)
    logger = get_backend_logger(BACKEND_NAME, LOGS_DIR)
    logger.info(f"Starting automation for backend: {BACKEND_NAME}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            # Step 1: Open login page
            logger.info(f"Opening login page: {LOGIN_URL}")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            # Step 2: Solve & fill the captcha
            logger.info("Waiting for captcha image...")
            page.wait_for_selector("img#ImageCheck", timeout=10_000)
            captcha_code = solve_captcha_with_retries(page, save_dir=DATA_DIR, tries=3, timeout=10_000)
            logger.info(f"Captcha recognized as: {captcha_code}")
            page.fill("#txtVerifyCode", captcha_code)

            # Step 3: Fill login credentials & submit
            page.wait_for_selector("#txtLoginName", timeout=10_000)
            page.fill("#txtLoginName", USERNAME)
            page.fill("#txtLoginPass", PASSWORD)
            page.wait_for_timeout(1500)
            page.click("#btnLogin")
            logger.info("Submitted login form. Waiting for left‐nav iframe...")
            page.wait_for_selector("iframe#frm_left_frm", timeout=20_000)

            # Step 4: Switch into left‐nav frame and click “User Management”
            left_iframe_el = page.query_selector("iframe#frm_left_frm")
            if not left_iframe_el:
                raise Exception("Cannot find <iframe id='frm_left_frm'>")
            left_frame = left_iframe_el.content_frame()
            if not left_frame:
                raise Exception("Could not obtain content_frame() for left iframe.")

            left_frame.wait_for_selector("xpath=//span[contains(text(),'User Management')]", timeout=10_000)
            left_frame.click("xpath=//span[contains(text(),'User Management')]")
            logger.info("Clicked 'User Management'.")
            page.wait_for_timeout(1000)

            # Step 5: Switch to main‐content frame & click “Create Player”
            page.wait_for_selector("iframe#frm_main_content", timeout=10_000)
            main_iframe_el = page.query_selector("iframe#frm_main_content")
            if not main_iframe_el:
                raise Exception("Cannot find <iframe id='frm_main_content'>")
            main_frame = main_iframe_el.content_frame()
            if not main_frame:
                raise Exception("Could not obtain content_frame() for main iframe.")

            logger.info("Switched to main content frame.")
            main_frame.wait_for_selector("xpath=//a[contains(text(),'Create Player')]", timeout=10_000)
            main_frame.click("xpath=//a[contains(text(),'Create Player')]")
            logger.info("Clicked 'Create Player' button.")
            page.wait_for_timeout(2000)

            # Step 6: Switch to CreateAccount dialog iframe
            iframe_dialog_el = page.wait_for_selector(
                "xpath=//iframe[contains(@src,'CreateAccount.aspx')]", timeout=15_000
            )
            dialog_frame = iframe_dialog_el.content_frame()
            if not dialog_frame:
                raise Exception("Failed to switch to Create Player iframe.")
            logger.info("Switched to Create Player iframe.")

            # Step 7: Fill out and submit “Create Player” form
            new_username, new_password = generate_credentials()
            dialog_frame.wait_for_selector("#txtAccount", timeout=10_000)
            dialog_frame.fill("#txtAccount", new_username)
            dialog_frame.wait_for_timeout(1000)
            dialog_frame.fill("#txtLogonPass", new_password)
            dialog_frame.wait_for_timeout(500)
            dialog_frame.fill("#txtLogonPass2", new_password)
            dialog_frame.wait_for_timeout(1500)

            # Step 8: Click final “Create Player”
            final_btn = dialog_frame.locator(
                "xpath=//a[contains(@class,'btn13') and contains(text(),'Create Player')]"
            )
            final_btn.wait_for(state="visible", timeout=10_000)
            final_btn.click()
            logger.info(f"Submitted Create Player form for {new_username}.")
            page.wait_for_timeout(3000)

            # Step 9: Save credentials to file
            created_file = DATA_DIR / "created_players.txt"
            with created_file.open("a", encoding="utf-8") as f:
                f.write(f"{new_username}:{new_password}\n")
            logger.info(f"✅ Created player saved: {new_username}:{new_password}")

        except PlaywrightTimeoutError as te:
            logger.error(f"TimeoutError: {te}")
        except Exception as e:
            logger.exception(f"Unhandled Exception: {e}")
        finally:
            logger.info("Automation run completed. Browser remains open (headed mode).")
            # If you want browser to close automatically, uncomment:
            # browser.close()
