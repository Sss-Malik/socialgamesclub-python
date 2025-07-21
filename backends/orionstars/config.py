# backends/orionstars/config.py

from pathlib import Path


# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "orionstars"
BACKEND_ID = 6
DEBUG = True
BACKEND_SIGNATURE = "OS"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://orionstars.vip:8781/default.aspx"
MAIN_URL = "https://orionstars.vip:8781/Cashier.aspx"
USERNAME  = "TestOS159"
PASSWORD  = "Test@159872!!"

CAPTCHA = True
URL_CHANGE = False
USER_MANAGEMENT_URL = None

MAX_CAPTCHA_RETRIES = 5


# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"

# Ensure data/ and logs/ exist (automation will call .mkdir())

# Selectors
LOGIN_ACCOUNT = "#txtLoginName"
LOGIN_PASSWORD = "#txtLoginPass"
LOGIN_BUTTON = "#btnLogin"
CAPTCHA_IMG = "img#ImageCheck"
CAPTCHA_INPUT = "#txtVerifyCode"
MAIN_PAGE_EL = "iframe#frm_main_content"
LEFT_IFRAME = "iframe#frm_main_content"
USER_MANAGEMENT_XPATH = "xpath=//span[contains(text(),'User Management')]"
MAIN_IFRAME = "iframe#frm_main_content"
CREATE_ACCOUNT_INIT = "xpath=//a[contains(text(),'Create Player')]"
CREATE_ACCOUNT_DIALOG = "xpath=//iframe[contains(@src,'CreateAccount.aspx')]"
ACCOUNT_ID = "#txtAccount"
ACCOUNT_PASSWORD = "#txtLogonPass"
CONFIRM_PASSWORD = "#txtLogonPass2"
CREATE_ACCOUNT = "xpath=//a[contains(@class,'btn13') and contains(text(),'Create Player')]"
ACCOUNT_SUCCESS = '.el-message.el-message--success'
ACCOUNT_SEARCH_INPUT = 'input[name="txtSearch"]'
ACCOUNT_SEARCH_BUTTON = "a:has-text('Search')"