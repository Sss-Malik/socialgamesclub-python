import random
import string

from backends.gameroom.config import BACKEND_SIGNATURE

def generate_credentials():
    # Validate that BACKEND_SIGNATURE contains at least one letter
    if not any(c.isalpha() for c in BACKEND_SIGNATURE):
        raise ValueError("BACKEND_SIGNATURE must contain at least one letter")

    # Generate account_id
    prefix = "user"
    max_total_length = 13
    remaining_length = max_total_length - len(prefix) - len(BACKEND_SIGNATURE)

    if remaining_length <= 0:
        raise ValueError("BACKEND_SIGNATURE is too long to fit in account_id")

    def generate_account_id():
        while True:
            random_number = ''.join(random.choices(string.digits, k=remaining_length))
            idx = f"{prefix}{BACKEND_SIGNATURE}{random_number}"
            if any(c.isdigit() for c in idx) and any(c.isalpha() for c in idx):
                return idx

    account_id = generate_account_id()

    # Generate password with letters and digits only, between 6 to 12 characters
    def generate_password():
        while True:
            password_length = random.randint(6, 12)
            password_chars = string.ascii_letters + string.digits
            password = ''.join(random.choices(password_chars, k=password_length))
            if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
                return password

    password = generate_password()

    return account_id, password
