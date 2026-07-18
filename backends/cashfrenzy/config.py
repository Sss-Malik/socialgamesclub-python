from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "cashfrenzy"
BACKEND_ID = 14
DEBUG = True
BACKEND_SIGNATURE = "CF"

# — Direct HTTP API base host (Laravel + JWT; same vendor software as gameroom) —
API_BASE_URL = "https://agentserver.cashfrenzy777.com"

# — Login credentials —
# Real credentials live in the Laravel-managed `backend_games` row and reach
# the client via build_client_from_backend(), which prefers backend.username /
# backend.password. These are empty fallbacks — never commit live credentials.
USERNAME = ""
PASSWORD = ""

# — Eligibility thresholds (consistent with the other API-based backends) —
RECHARGE_ELIGIBLE_THRESHOLD = 20  # recharge only when player balance <= this
FREEPLAY_ELIGIBLE_THRESHOLD = 5   # freeplay only when player balance < this

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"
