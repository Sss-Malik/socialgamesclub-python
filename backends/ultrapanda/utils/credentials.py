# import random
# import string
#
# from backends.ultrapanda.config import BACKEND_SIGNATURE
#
# def generate_credentials():
#     # Constants
#     special_chars = "!@#$%^/.,()"
#     min_id_len, max_id_len = 7, 16
#     min_pwd_len, max_pwd_len = 6, 16
#
#     # Generate account_id
#     prefix = f"user{BACKEND_SIGNATURE}"
#     remaining_len = max(min_id_len, len(prefix) + 1)  # ensure at least 1 digit
#     max_digits = max_id_len - len(prefix)
#     if max_digits < 1:
#         raise ValueError("backend_signature is too long to create a valid account_id")
#
#     random_digits = str(random.randint(10**(max_digits-1), 10**max_digits - 1))
#     account_id = (prefix + random_digits)[:max_id_len]
#
#     # Generate password
#     pwd_len = random.randint(min_pwd_len, max_pwd_len)
#
#     # Ensure at least 1 letter, 1 number, and 1 special character
#     password_chars = [
#         random.choice(string.ascii_letters),
#         random.choice(string.digits),
#         random.choice(special_chars)
#     ]
#
#     # Fill the rest with a mix of allowed characters
#     all_chars = string.ascii_letters + string.digits + special_chars
#     password_chars += random.choices(all_chars, k=pwd_len - 3)
#     random.shuffle(password_chars)
#     password = ''.join(password_chars)
#
#     return account_id, password



import random
import string

from backends.ultrapanda.config import BACKEND_SIGNATURE

def generate_credentials():
    # Generate account_id
    # Prefix: "user_"
    # backend_signature: passed as argument
    # random number: ensure total length <= 13
    prefix = "user"
    max_total_length = 13
    remaining_length = max_total_length - len(prefix) - len(BACKEND_SIGNATURE)

    if remaining_length <= 0:
        raise ValueError("backend_signature is too long to fit in account_id")

    random_length = random.randint(1, remaining_length)

    random_number = ''.join(random.choices(string.digits, k=random_length))
    account_id = f"{prefix}{BACKEND_SIGNATURE}{random_number}"

    def generate_password():
        # a small pool of user-friendly words (you can expand this)
        words = [
            "apple", "banana", "cherry", "orange", "tomato", "grape", "mango",
            "sunset", "cloud", "river", "forest", "ocean", "mountain",
            "shadow", "light", "storm", "rain", "flame", "stone", "leaf"
        ]

        # randomly choose a word length range 4–6 letters
        filtered_words = [w for w in words if 4 <= len(w) <= 6]

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