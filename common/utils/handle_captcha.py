import logging
from common.utils.anticaptcha_solver import solve_captcha

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def handle_captcha(page, logger: logging.Logger, captcha_img, captcha_input, save_dir, timeout=10_000):
    try:
        captcha_el = page.wait_for_selector(captcha_img, timeout=timeout)
        logger.info("CAPTCHA detected. Solving...")
        text = solve_captcha(captcha_el, save_dir, logger)

        if isinstance(text, str) and text:
            page.fill(captcha_input, text)
            logger.info("CAPTCHA filled with: %s", text)
        else:
            logger.warning("Solver returned invalid text: %s", text)

    except PlaywrightTimeoutError:
        logger.info("No CAPTCHA found within timeout (%sms).", timeout)
    except Exception as e:
        logger.exception("Unexpected CAPTCHA handling error: %s", e)