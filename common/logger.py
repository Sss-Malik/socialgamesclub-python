# common/logger.py

import logging
import sys
from pathlib import Path

def get_backend_logger(backend_name: str, logs_dir: Path) -> logging.Logger:
    """
    Create (or retrieve) a logger for a given backend.
    - Writes to both stdout and a file at logs_dir/automation.log
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / "automation.log"

    logger = logging.getLogger(f"casino_automation.{backend_name}")
    logger.setLevel(logging.DEBUG)

    # If handlers already set up, just return existing logger:
    if logger.handlers:
        return logger

    # Console handler (INFO+)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

    # File handler (DEBUG+)
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    return logger
