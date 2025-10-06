import random
import string

from backends.firekirin.config import BACKEND_SIGNATURE

def generate_credentials():
    def random_number_str(max_len):
        # Leave room for 'user_' and backend_signature
        max_num_len = 13 - (len("user_") + len(BACKEND_SIGNATURE))
        return ''.join(random.choices(string.digits, k=random.randint(1, max_num_len)))

    # (old func)
    # def generate_password():
    #     chars = string.ascii_letters + string.digits + "_"
    #     return ''.join(random.choices(chars, k=random.randint(8, 12)))

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

    number_part = random_number_str(13)
    account_id = f"user_{BACKEND_SIGNATURE}{number_part}"
    password = generate_password()
    return account_id, password
