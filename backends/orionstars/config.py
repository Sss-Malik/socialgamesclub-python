# backends/orionstars/config.py

from pathlib import Path

# — Backend identification (used for logging, data paths, etc.) —
BACKEND_NAME = "orionstars"

# — Login credentials (hard-coded for this backend) —
LOGIN_URL = "https://orionstars.vip:8781/default.aspx"
USERNAME  = "TestOS159"
PASSWORD  = "Test@159872!!"

# — Paths for this backend (relative to the project root) —
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure data/ and logs/ exist (automation will call .mkdir())
