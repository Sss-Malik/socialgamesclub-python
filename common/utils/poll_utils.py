import time
from common.utils.db_actions import get_latest_valid_session, get_session
from db import SessionLocal
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

def wait_for_valid_session(backend, logger, timeout=30, interval=3):
    """
    Polls for a valid backend session every `interval` seconds until `timeout` is reached.
    Returns the session if found, otherwise raises TimeoutError.
    """
    start = time.time()
    while time.time() - start < timeout:
        session = get_latest_valid_session(backend)
        if session:
            logger.info(f"Valid session found for backend '{backend}'")
            return session
        logger.info(f"No valid session yet for backend '{backend}', retrying in {interval} seconds...")
        time.sleep(interval)

    return None


import time
import logging


def wait_for_active_tasks_to_zero(session_id: int, page: Page, max_wait_seconds: int = 40, poll_interval: int = 2,
                                  logger: logging.Logger = None) -> bool:
    db = SessionLocal()
    try:
        waited = 0
        session = get_session(session_id, db=db)
        if not session:
            if logger:
                logger.warning(f"No session found with id {session_id}")
            return False

        while waited < max_wait_seconds:
            db.refresh(session)
            active_tasks = session.active_tasks_count or 0
            if active_tasks == 0:
                if logger:
                    logger.info("Session now free. Proceeding.")
                return True

            if logger:
                logger.warning(f"Session is currently in use ({active_tasks} active tasks). Waiting...")

            page.wait_for_timeout(poll_interval * 1000)  # <-- FIXED
            waited += poll_interval

        if logger:
            logger.warning(f"Timeout reached waiting for session {session_id} to become free.")
        return False
    finally:
        db.close()
