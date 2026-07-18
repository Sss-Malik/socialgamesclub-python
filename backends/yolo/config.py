from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "yolo"
BACKEND_ID = 13
DEBUG = True
BACKEND_SIGNATURE = "YL"

# — Direct HTTP API base URL (Laravel 7 + Dcat Admin; no trailing slash) —
API_BASE_URL = "https://agent.yolo-777.com"

# — Login credentials (fallback when backend_games columns are unset) —
USERNAME = "webyolo1"
PASSWORD = "Web@@1122"

# Default RegisterIP for new players — the server REQUIRES this field to be
# present (insert fails with a DB error otherwise); any value works.
DEFAULT_REGISTER_IP = "0.0.0.0"

# — Eligibility thresholds (consistent with the other API-based backends) —
RECHARGE_ELIGIBLE_THRESHOLD = 20  # recharge only when player score <= this
FREEPLAY_ELIGIBLE_THRESHOLD = 5   # freeplay only when player score < this

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"
