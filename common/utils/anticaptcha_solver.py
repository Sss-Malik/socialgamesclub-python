from pathlib import Path
import time
from anticaptchaofficial.imagecaptcha import *

import logging

def solve_captcha(captcha_img_el, save_dir: Path, logger: logging.Logger, timeout: int = 10000) -> str | int:
    try:
        ts = int(time.time())
        img_path = save_dir / f"captcha_raw_{ts}.png"

        # Capture CAPTCHA image
        captcha_img_el.screenshot(type="png", path=str(img_path))
        logger.info(f"Captcha screenshot saved to {img_path}")

        # Initialize solver
        solver = imagecaptcha()
        solver.set_verbose(1)
        solver.set_key("8f05b2c530919c55206b1292e565b7ef")
        solver.set_soft_id(0)

        # Attempt to solve
        captcha_text = solver.solve_and_return_solution(str(img_path))

        if captcha_text == 0:
            logger.error("Captcha solver failed to return a valid solution.")
        return captcha_text

    except Exception as e:
        logger.exception(f"Exception occurred while solving captcha: {e}")
        return 0
