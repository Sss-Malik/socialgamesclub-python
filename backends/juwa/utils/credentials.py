import random
import string

from backends.juwa.config import BACKEND_SIGNATURE
from settings import WORDS_FOR_PASSWORD

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

    random_length = random.randint(1, remaining_length)

    random_number = ''.join(random.choices(string.digits, k=random_length))
    account_id = f"{prefix}{BACKEND_SIGNATURE}{random_number}"

    def generate_password():

        # randomly choose a word length range 4–6 letters
        filtered_words = [w for w in WORDS_FOR_PASSWORD if 4 <= len(w) <= 6]

        # pick one random word
        word = random.choice(filtered_words)

        # append a random 1–3 digit number
        number = str(random.randint(1, 999))

        # combine them
        return f"{word}{number}"

    # Generate password (length 8 to 16, only letters, digits, underscores)
    # password_length = random.randint(8, 16)
    # password_chars = string.ascii_letters + string.digits + "_"
    password = generate_password()

    return account_id, password