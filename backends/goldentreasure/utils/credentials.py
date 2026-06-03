import random
import string

from backends.goldentreasure.config import BACKEND_SIGNATURE
from settings import WORDS_FOR_PASSWORD


def generate_credentials():
    """Return (account_id, password) valid for Golden Treasure's savePlayer /
    updatePlayer policy.

    Password policy (spec §8.3): 6-16 characters, must combine letters and
    numbers. The word (>=4 letters) + 2-3 digit number guarantees both a
    letter and a digit and a length of at least 6.
    """
    # account_id: "user" + signature + digits, total length <= 13.
    # Kept strictly alphanumeric (no underscore) to stay within the
    # account-name policy.
    prefix = "user"
    max_total_length = 13
    remaining_length = max_total_length - len(prefix) - len(BACKEND_SIGNATURE)
    if remaining_length <= 0:
        raise ValueError("BACKEND_SIGNATURE is too long to fit in account_id")

    random_length = random.randint(1, remaining_length)
    random_number = "".join(random.choices(string.digits, k=random_length))
    account_id = f"{prefix}{BACKEND_SIGNATURE}{random_number}"

    def generate_password():
        # word of 4-6 letters keeps the result within the 6-16 char policy
        filtered_words = [w for w in WORDS_FOR_PASSWORD if 4 <= len(w) <= 6]
        word = random.choice(filtered_words)
        # 2-3 digit number => combined length is always >= 6
        number = str(random.randint(10, 999))
        return f"{word}{number}"

    return account_id, generate_password()
