from pathlib import Path
import time
from anticaptchaofficial.imagecaptcha import *
from settings import ANTICAPTCHA_API_KEY
import logging

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

