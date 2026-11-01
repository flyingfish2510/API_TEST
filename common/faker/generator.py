import random
import string


class FakeDataGenerator:
    @staticmethod
    def random_string(length: int = 8) -> str:
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k=length))

    @staticmethod
    def random_int(min_val: int = 0, max_val: int = 100) -> int:
        return random.randint(min_val, max_val)