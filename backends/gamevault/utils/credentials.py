import random
import string

from backends.gamevault.config import BACKEND_SIGNATURE

def generate_credentials():
    # Generate account_id
    # Prefix: "user_"
    # backend_signature: passed as argument
    # random number: ensure total length <= 13
    prefix = "user_"
    max_total_length = 13
    remaining_length = max_total_length - len(prefix) - len(BACKEND_SIGNATURE)

    if remaining_length <= 0:
        raise ValueError("backend_signature is too long to fit in account_id")

    random_number = ''.join(random.choices(string.digits, k=remaining_length))
    account_id = f"{prefix}{BACKEND_SIGNATURE}{random_number}"

    # Generate password (length 8 to 16, only letters, digits, underscores)
    password_length = random.randint(8, 16)
    password_chars = string.ascii_letters + string.digits + "_"
    password = ''.join(random.choices(password_chars, k=password_length))

    return account_id, password