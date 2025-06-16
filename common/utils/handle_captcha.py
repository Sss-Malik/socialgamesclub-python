import logging
from common.utils.anticaptcha_solver import solve_captcha

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def handle_captcha(page, logger: logging.Logger, captcha_img, save_dir, timeout=10_000):
    try:
        captcha_el = page.wait_for_selector(captcha_img, timeout=timeout)
        logger.info("CAPTCHA detected. Solving...")
        text, solver = solve_captcha(captcha_el, save_dir, logger)

        if isinstance(text, str) and text:
            logger.info("CAPTCHA returned as: %s", text)
            return text, solver  # Return solver for later reporting
        else:
            logger.warning("Solver returned invalid text: %s", text)
            return text, solver  # Still return solver to potentially report
    except PlaywrightTimeoutError:
        logger.info("No CAPTCHA found within timeout (%sms).", timeout)
    except Exception as e:
        logger.exception("Unexpected CAPTCHA handling error: %s", e)
    return 0, None
