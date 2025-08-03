import shutil
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

def cleanup_backend_dirs(backends_root: Path):
    """
    Deletes the entire 'data' and 'logs' subdirectories
    for each backend under backends_root, then recreates them.
    """
    for backend in backends_root.iterdir():
        if not backend.is_dir():
            continue
        for subdir in ("data", "logs"):
            dir_path = backend / subdir
            if dir_path.exists():
                try:
                    shutil.rmtree(dir_path)
                    logger.info(f"Deleted directory {dir_path}")
                except Exception:
                    logger.exception(f"Failed to delete directory {dir_path}")
            # recreate empty directory so your code can immediately start writing again
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Re-created directory {dir_path}")
            except Exception:
                logger.exception(f"Failed to re-create directory {dir_path}")
