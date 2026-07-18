import random
import string

from backends.cashfrenzy.config import BACKEND_SIGNATURE
from settings import WORDS_FOR_PASSWORD


def generate_credentials():
    """Return (account_id, password) valid for CashFrenzy's playerInsert /
    reset policy (spec §6, §13).

    - Account: letters + numbers only, 5-20 chars, unique. `user<SIG><digits>`
      is alphanumeric and 7-13 chars (within 5-20).
    - Password: letters + numbers only, 6-16 chars. word(4-6) + 2-3 digit
      number is alphanumeric and always 6-16 chars.
    """
    prefix = "user"
    max_total_length = 13
    remaining_length = max_total_length - len(prefix) - len(BACKEND_SIGNATURE)
    if remaining_length <= 0:
        raise ValueError("BACKEND_SIGNATURE is too long to fit in account_id")

    random_length = random.randint(1, remaining_length)
    random_number = "".join(random.choices(string.digits, k=random_length))
    account_id = f"{prefix}{BACKEND_SIGNATURE}{random_number}"

    def generate_password():
        filtered_words = [w for w in WORDS_FOR_PASSWORD if 4 <= len(w) <= 6]
        word = random.choice(filtered_words)
        number = str(random.randint(10, 999))  # 2-3 digits => length always >= 6
        return f"{word}{number}"

    return account_id, generate_password()
