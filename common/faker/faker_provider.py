# -*- coding: utf-8 -*-
"""
common/faker/faker_provider.py
Faker 数据生成器（最小可用版）
"""
from faker import Faker as _Faker
import uuid as _uuid
import random as _random


class FakerProvider:
    """Faker 数据生成器"""

    def __init__(self, locale: str = "zh_CN"):
        self._faker = _Faker(locale)

    # ---- 常用字段 ----
    def name(self):
        return self._faker.name()

    def phone(self):
        return self._faker.phone_number()

    def email(self):
        return self._faker.email()

    def address(self):
        return self._faker.address()

    def company(self):
        return self._faker.company()

    def password(self):
        return self._faker.password()

    def uuid(self):
        return _uuid.uuid4().hex

    def user_name(self):
        return self._faker.user_name()

    # ---- 唯一值 ----
    def unique_phone(self):
        return self._faker.unique.phone_number()

    def unique_email(self):
        return self._faker.unique.email()

    def unique_user_name(self):
        return self._faker.unique.user_name()

    def unique_name(self, prefix: str = "test"):
        return f"{prefix}_{_uuid.uuid4().hex[:8]}"

    # ---- 正则 ----
    def regexify(self, pattern: str):
        return self._faker.regexify(pattern)

    # ---- 随机值 ----
    def random_int(self, min_val: int = 1, max_val: int = 100):
        return _random.randint(min_val, max_val)

    def random_choice(self, choices: list):
        return _random.choice(choices)

    # ---- 动态调用 ----
    def call(self, method: str, *args, **kwargs):
        if not hasattr(self, method) and not hasattr(self._faker, method):
            raise AttributeError(f"Faker 无此方法: {method}")
        if hasattr(self, method):
            return getattr(self, method)(*args, **kwargs)
        return getattr(self._faker, method)(*args, **kwargs)


# ==== 关键：对外暴露的全局单例 ====
faker = FakerProvider()