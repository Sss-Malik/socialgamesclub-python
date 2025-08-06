from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "juwa"
BACKEND_ID = 4
DEBUG = True
BACKEND_SIGNATURE = "JW"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://ht.juwa777.com/HomeDetail"
USERNAME  = "TestJW159"
PASSWORD  = "Test1234"

CAPTCHA = True
URL_CHANGE = True

USER_MANAGEMENT_URL = "https://ht.juwa777.com/userManagement"
MAX_CAPTCHA_RETRIES = 5

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"

# Ensure data/ and logs/ and data/captcha exist (automation will call .mkdir())

# Selectors
LOGIN_ACCOUNT = 'input[placeholder="Please enter your account"]'
LOGIN_PASSWORD = 'input[placeholder="Please enter your password"]'
CAPTCHA_IMG = "img.imgCode"
CAPTCHA_INPUT = 'input[placeholder="Please enter the verification code"]'
LOGIN_BUTTON = 'button:has-text("Sign in")'
MAIN_PAGE_EL = 'button:has-text("log out")'
CREATE_ACCOUNT_INIT = 'button:has-text("create")'
ACCOUNT_ID = '//label[text()="Account"]/following-sibling::div//input'
ACCOUNT_PASSWORD = '//label[text()="Login password"]/following-sibling::div//input[@type="password"]'
CONFIRM_PASSWORD = '//label[text()="Confirm password"]/following-sibling::div//input[@type="password"]'
CREATE_ACCOUNT = 'button:has-text("Save")'
ACCOUNT_SUCCESS = '.el-message--success .el-message__content'
ACCOUNT_SUCCESS_MSG = ["success"]
ACCOUNT_SEARCH_INPUT = 'input[placeholder="Please enter your search content"]'
