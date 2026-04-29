from pathlib import Path
import time
from typing import Optional

from anticaptchaofficial.imagecaptcha import *
from anticaptchaofficial.recaptchav3proxyless import recaptchaV3Proxyless
from settings import ANTICAPTCHA_API_KEY
import logging


def solve_recaptcha_v3(
    *,
    website_url: str,
    website_key: str,
    page_action: str,
    min_score: float,
    logger: logging.Logger,
    is_enterprise: bool = False,
    user_agent: Optional[str] = None,
) -> Optional[str]:
    """Solve reCAPTCHA v3 (or v3 Enterprise) via anticaptcha.

    Returns the g-recaptcha-response token on success, or None on failure.
    """
    try:
        solver = recaptchaV3Proxyless()
        solver.set_key(ANTICAPTCHA_API_KEY)
        solver.set_website_url(website_url)
        solver.set_website_key(website_key)
        solver.set_page_action(page_action)
        solver.set_min_score(min_score)
        if is_enterprise:
            solver.set_is_enterprise(True)
        if user_agent:
            solver.set_user_agent(user_agent)

        token = solver.solve_and_return_solution()
        if token == 0:
            logger.error(
                "reCAPTCHA v3 solve failed: %s",
                getattr(solver, "error_code", "unknown"),
            )
            return None
        return token
    except Exception as e:
        logger.critical("Exception during reCAPTCHA v3 solve: %s", e, exc_info=True)
        return None


def solve_captcha(captcha_img_el, save_dir: Path, logger: logging.Logger, timeout: int = 10000):
    try:
        ts = int(time.time())
        img_path = save_dir / f"captcha_raw_{ts}.png"

        # Capture CAPTCHA image
        captcha_img_el.screenshot(type="png", path=str(img_path))
        logger.debug(f"Captcha screenshot saved to {img_path}")

        # Initialize solver
        solver = imagecaptcha()

        solver.set_key(ANTICAPTCHA_API_KEY)

        solver.set_numeric(1)


        # Solve CAPTCHA
        captcha_text = solver.solve_and_return_solution(str(img_path))


        # Return both solution and solver object
        return captcha_text, solver

    except Exception as e:
        logger.critical(f"Exception occurred while solving captcha: {e}", exc_info=True)
        return 0, None

