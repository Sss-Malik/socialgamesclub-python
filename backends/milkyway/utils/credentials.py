import random
import string

from backends.milkyway.config import BACKEND_SIGNATURE

def generate_credentials():
    def random_number_str(max_len):
        # Leave room for 'user_' and backend_signature
        max_num_len = 13 - (len("user_") + len(BACKEND_SIGNATURE))
        return ''.join(random.choices(string.digits, k=random.randint(1, max_num_len)))

    def generate_password():
        chars = string.ascii_letters + string.digits + "_"
        return ''.join(random.choices(chars, k=random.randint(8, 12)))

    number_part = random_number_str(13)
    account_id = f"user_{BACKEND_SIGNATURE}{number_part}"
    password = generate_password()
    return account_id, password
