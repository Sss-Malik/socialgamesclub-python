from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "goldentreasure"
BACKEND_ID = 11
DEBUG = True
BACKEND_SIGNATURE = "GT"

# — API endpoint / crypto configuration (see goldentreasure_api_findings.md) —
API_BASE_URL = "https://agent.goldentreasure.mobi/api"
ORIGIN = "https://agent.goldentreasure.mobi"
SIGN_SECRET = "#s3LEA3RpR6PNmbWtuBCPn!4gS2DNM44"

# — Login credentials (fallback when backend_games columns are unset) —
USERNAME = "Test02Gd1WEB"
PASSWORD = "Zaeem@1233"

# — Rate limiting: the API answers code 167 on bursts of mutating calls
#   (enterScore / savePlayer). Space them by at least this many seconds. —
RATE_LIMIT_DELAY_SECONDS = 5

# — Eligibility thresholds (consistent with gamevault / gameroom) —
RECHARGE_ELIGIBLE_THRESHOLD = 20  # recharge only when player balance <= this
FREEPLAY_ELIGIBLE_THRESHOLD = 5   # freeplay only when player balance < this

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"
