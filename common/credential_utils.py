# common/credential_utils.py

import random
import string

def generate_credentials() -> (str, str):
    """
    Return (username, password), where:
      - username: random 8–13 chars from [A-Za-z0-9_]
      - password: random 10–16 chars from [A-Za-z0-9_]
    """
    letters_digits = string.ascii_letters + string.digits + "_"
    username = "".join(random.choices(letters_digits, k=random.randint(8, 13)))
    password = "".join(random.choices(letters_digits, k=random.randint(10, 16)))
    return username, password
