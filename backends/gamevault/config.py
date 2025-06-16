from pathlib import Path

BACKEND_NAME = "gamevault"
BACKEND_SIGNATURE = "GV"
DEBUG = False
MAX_CAPTCHA_RETRIES = 5


LOGIN_URL = "https://agent.gamevault999.com/login"
USERNAME  = "TestGV159"
PASSWORD  = "Test1234"
CAPTCHA = True
URL_CHANGE = True
USER_MANAGEMENT_URL = "https://agent.gamevault999.com/userManagement"

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"


# Selectors
LOGIN_ACCOUNT = 'input[placeholder="username"]'
LOGIN_PASSWORD = 'input[placeholder="password"]'
CAPTCHA_IMG = "img.imgCode"
CAPTCHA_INPUT = "div.loginCode input"
LOGIN_BUTTON = 'button:has-text("Sign in")'
MAIN_PAGE_EL = 'button:has-text("Log out")'
CREATE_ACCOUNT_INIT = 'button:has-text("New Account")'
ACCOUNT_ID = "label:has-text('Account') ~ .el-form-item__content input.el-input__inner"
ACCOUNT_PASSWORD = "label:has-text('Login password') ~ .el-form-item__content input.el-input__inner"
CONFIRM_PASSWORD = "label:has-text('Confirm password') ~ .el-form-item__content input.el-input__inner"
CREATE_ACCOUNT = 'button:has-text("Save")'
ACCOUNT_SUCCESS = ".el-dialog:has(#invoiceModel)"
ACCOUNT_SUCCESS_MSG = ["successfully"]
ERROR_EL = "div.el-message--error"

ACCOUNT_SEARCH_INPUT = 'input[placeholder="Please enter your search content"]'

