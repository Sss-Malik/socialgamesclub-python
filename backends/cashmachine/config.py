# backends/cashmachine/config.py

from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "cashmachine"
# NOTE: must match the `id` of this backend's row in the DB `backends` table.
BACKEND_ID = 15
DEBUG = True
BACKEND_SIGNATURE = "CM"

# — Login credentials —
# The real username/password are stored in the Laravel-managed DB `backends`
# row and passed through to the client via build_client_from_backend()
# (which prefers backend.username / backend.password). These constants are
# only empty fallbacks — never commit live credentials here.
LOGIN_URL = "https://agentserver.cashmachine777.com/admin/login"
USERNAME  = ""
PASSWORD  = ""
MAX_CAPTCHA_RETRIES = 5

# — Direct HTTP API base URL (no trailing slash, no path) —
# Cash Machine 777 is the same white-label "layui" agent panel as gameroom and
# exposes the same JWT + form-urlencoded JSON API on this host.
API_BASE_URL = "https://agentserver.cashmachine777.com"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
CAPTCHA_DIR = DATA_DIR / "captcha"
UTILS_DIR = BASE_DIR / "utils"
