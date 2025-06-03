from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "juwa"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://ht.juwa777.com/login"
USERNAME  = "TestJW159"
PASSWORD  = "Test1234"
USER_MANAGEMENT_URL = "https://ht.juwa777.com/userManagement"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure data/ and logs/ exist (automation will call .mkdir())
