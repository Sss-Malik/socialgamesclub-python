from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "gameroom"
DEBUG = True
BACKEND_SIGNATURE = "GR"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://agentserver1.gameroom777.com/admin/login"
USERNAME  = "TestGR159"
PASSWORD  = "TestGR1122@"
MAX_CAPTCHA_RETRIES = 5

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"

# Ensure data/ and logs/ and data/captcha exist (automation will call .mkdir())

# Selectors
LOGIN_ACCOUNT = 'input[name="username"]'
LOGIN_PASSWORD = 'input[name="password"]'
CAPTCHA_IMG = "div#captchaImg"
CAPTCHA_INPUT = 'input[name="captcha"]'
LOGIN_BUTTON = 'button:has-text("Login")'
MAIN_PAGE_EL = 'a#logout'
USER_MANAGEMENT_EL = 'a[data-url="/admin/player/index"]'
MAIN_IFRAME = 'iframe[src="/admin/player/index"]'
CREATE_ACCOUNT_INIT = 'button[lay-event="add"]'
DIALOG_IFRAME = 'iframe[src="https://agentserver1.gameroom777.com/admin/player/insert"]'
ACCOUNT_ID = 'input[name="username"][type="text"][autocomplete="off"][lay-verify="required"]'
ACCOUNT_BALANCE = 'input[name="money"]'
ACCOUNT_PASSWORD = 'input#password[name="password"]'
CONFIRM_PASSWORD = 'input[name="password_confirmation"]'
CREATE_ACCOUNT = 'button:has-text("Submit")'
ACCOUNT_SUCCESS = "div.layui-layer.layui-layer-dialog"
ACCOUNT_SUCCESS_CLOSE = 'a:has-text("Close")'
ACCOUNT_SEARCH_INPUT = 'input[name="account"]'
ACCOUNT_RECHARGE_SUCCESS = "div.layui-layer.layui-layer-dialog"
