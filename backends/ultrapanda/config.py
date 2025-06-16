from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "pandamaster"
DEBUG = False
BACKEND_SIGNATURE = "PM"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://ht.ultrapanda.mobi/#/login"
USERNAME  = "TestUP159"
PASSWORD  = "Test1234"
USER_MANAGEMENT_URL = "https://ht.ultrapanda.mobi/#/manage-user/account"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"

# Ensure data/ and logs/ exist (automation will call .mkdir())


# Selectors
LOGIN_ACCOUNT = 'input[name="userName"]'
LOGIN_PASSWORD = 'input[name="passWd"]'
LOGIN_BUTTON = 'button:has-text("Login")'
MAIN_PAGE_EL = 'section.app-main'
CREATE_ACCOUNT_INIT = 'button:has-text("Add Player")'
ACCOUNT_ID = 'input[placeholder="Player’s account name (7-16 characters)"]'
ACCOUNT_PASSWORD = 'input[placeholder="Length must be 6-16 characters! Must include a combination of numbers and letters, and allows some special characters: !@#$%^/.,()"]'
CREATE_ACCOUNT = 'button:has-text("OK")'
ACCOUNT_SUCCESS = '.el-message.el-message--success'
ACCOUNT_SUCCESS_MSG = ["sucessful"]