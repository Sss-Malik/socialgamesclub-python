from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "vblink"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://gm.vblink777.club/#/login"
USERNAME  = "TestVB159"
PASSWORD  = "Test12345"
USER_MANAGEMENT_URL = "https://gm.vblink777.club/#/manage-user/account"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure data/ and logs/ exist (automation will call .mkdir())
