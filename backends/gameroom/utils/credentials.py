import random
import string

from backends.gameroom.config import BACKEND_SIGNATURE
from settings import WORDS_FOR_PASSWORD
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

    random_length = random.randint(0, remaining_length)

    def generate_account_id():
        while True:
            random_number = ''.join(random.choices(string.digits, k=random_length))
            idx = f"{prefix}{BACKEND_SIGNATURE}{random_number}"
            if any(c.isdigit() for c in idx) and any(c.isalpha() for c in idx):
                return idx

    account_id = generate_account_id()

    # Generate password with letters and digits only, between 6 to 12 characters
    # def generate_password():
    #     specials = "!#@"
    #     letters_upper = string.ascii_uppercase
    #     letters_lower = string.ascii_lowercase
    #     digits = string.digits
    #
    #     allowed_chars = letters_upper + letters_lower + digits + specials
    #
    #     while True:
    #         length = random.randint(6, 12)
    #         x = ''.join(random.choices(allowed_chars, k=length))
    #
    #         if (any(c.isupper() for c in x) and
    #                 any(c.islower() for c in x) and
    #                 any(c.isdigit() for c in x) and
    #                 any(c in specials for c in x)):
    #             return x

    def generate_password():
        # randomly choose a word length range 4–6 letters
        filtered_words = [w for w in WORDS_FOR_PASSWORD if 4 <= len(w) <= 6]

        # pick one random word
        word = random.choice(filtered_words)

        # append a random 1–3 digit number
        number = str(random.randint(1, 999))

        # combine them
        return f"{word}{number}"

    password = generate_password()

    return account_id, password
