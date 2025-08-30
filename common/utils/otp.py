import pyotp

def generate_2fa_code(secret_key: str) -> str:
    """
    Generate a 6-digit TOTP code using the Google Authenticator algorithm.

    Args:
        secret_key (str): The base32 encoded secret (e.g., 'VKGGGSM73DSUZ6VG').

    Returns:
        str: The 6-digit authentication code as a string.
    """
    totp = pyotp.TOTP(secret_key)
    return totp.now()