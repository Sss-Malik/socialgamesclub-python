import random
import string

def generate_credentials(backend_signature: str, include_special_char: bool = False):
    max_total_id_length = 13
    id_prefix = f"{backend_signature}_"
    max_id_body_length = max_total_id_length - len(id_prefix)

    def generate_account_id():
        while True:
            length = random.randint(7, max_id_body_length)
            # Ensure required characters
            required = (
                random.choice(string.ascii_letters) +
                random.choice(string.digits) +
                "_"
            )
            remaining_length = length - len(required)
            allowed = string.ascii_letters + string.digits + "_"
            random_chars = ''.join(random.choices(allowed, k=remaining_length))
            combined = list(required + random_chars)
            random.shuffle(combined)
            id_body = ''.join(combined)
            full_id = id_prefix + id_body
            if len(full_id) <= 13:
                return full_id

    def generate_password():
        while True:
            length = random.randint(6, 16)
            required = (
                random.choice(string.ascii_letters) +
                random.choice(string.digits) +
                "_"
            )
            remaining_length = length - len(required)
            allowed = string.ascii_letters + string.digits + "_"
            if include_special_char:
                allowed += "!@#$%^/.,()"
            random_chars = ''.join(random.choices(allowed, k=remaining_length))
            combined = list(required + random_chars)
            random.shuffle(combined)
            password = ''.join(combined)
            if (
                any(c.isalpha() for c in password) and
                any(c.isdigit() for c in password) and
                "_" in password
            ):
                return password

    account_id = generate_account_id()
    password = generate_password()
    return account_id, password