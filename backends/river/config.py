from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "river"
BACKEND_ID = 8
DEBUG = True
BACKEND_SIGNATURE = "RV"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://river-pay.com/office/login"
USERNAME  = "TestRS159"
PASSWORD  = "TestRS1122"
CAPTCHA = False
USER_MANAGEMENT_URL = "https://river-pay.com/cashier/create"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"

# Ensure data/ and logs/ exist (automation will call .mkdir())


# Selectors
LOGIN_ACCOUNT = 'input#LoginForm_login'
LOGIN_PASSWORD = 'input#LoginForm_password'
LOGIN_BUTTON = 'input[type="submit"][value="Log in"]'
MAIN_PAGE_EL = 'a[href="/office/logout"]'
USER_MANAGEMENT_EL = 'a[href="/cashier/create"]'
CREATE_ACCOUNT_INIT = 'input[type="submit"][value="Create account"]'
ACCOUNT_ID = 'input#Accounts_comments'
ACCOUNT_BALANCE = 'input#Accounts_balance'
ACCOUNT_PASSWORD = 'input[placeholder="Length must be 6-16 characters! Must include a combination of numbers and letters, and allows some special characters: !@#$%^/.,()"]'
CREATE_ACCOUNT = 'input[type="submit"][value="Create account"]'
ACCOUNT_SUCCESS = '.alert.alert-success'
ACCOUNT_SEARCH_INPUT = 'input[name="search"]'