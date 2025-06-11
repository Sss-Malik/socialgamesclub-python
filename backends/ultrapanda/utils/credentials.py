import random
import string

from backends.ultrapanda.config import BACKEND_SIGNATURE

def generate_credentials():
    # Constants
    special_chars = "!@#$%^/.,()"
    min_id_len, max_id_len = 7, 16
    min_pwd_len, max_pwd_len = 6, 16

    # Generate account_id
    prefix = f"user{BACKEND_SIGNATURE}"
    remaining_len = max(min_id_len, len(prefix) + 1)  # ensure at least 1 digit
    max_digits = max_id_len - len(prefix)
    if max_digits < 1:
        raise ValueError("backend_signature is too long to create a valid account_id")

    random_digits = str(random.randint(10**(max_digits-1), 10**max_digits - 1))
    account_id = (prefix + random_digits)[:max_id_len]

    # Generate password
    pwd_len = random.randint(min_pwd_len, max_pwd_len)

    # Ensure at least 1 letter, 1 number, and 1 special character
    password_chars = [
        random.choice(string.ascii_letters),
        random.choice(string.digits),
        random.choice(special_chars)
    ]

    # Fill the rest with a mix of allowed characters
    all_chars = string.ascii_letters + string.digits + special_chars
    password_chars += random.choices(all_chars, k=pwd_len - 3)
    random.shuffle(password_chars)
    password = ''.join(password_chars)

    return account_id, password
