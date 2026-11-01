# -*- coding: utf-8 -*-
"""
core/engine/variable_resolver.py
--------------------------------
变量解析引擎（VariableResolver）

职责：
    1. 递归扫描请求数据中的所有 {{...}} 占位符
    2. 根据命名空间路由到对应的工具（context / env / faker / time / crypto / random）
    3. 支持函数调用语法（带参数，如 {{time.offset_days(30)}}）
    4. 支持链式访问（如 {{env.accounts.admin.username}}）
    5. 解析失败时抛出明确异常

支持的语法：
    - 上下文变量:   {{token}} 或 {{context.token}}
    - 环境变量:     {{env.api.base_url}}
    - Faker生成:   {{faker.unique_phone}} / {{faker.name}}
    - 时间函数:     {{time.now_timestamp}} / {{time.offset_days(30)}}
    - 加解密:       {{crypto.md5('Test@123')}}
    - 随机数:       {{random.int(1, 100)}}
    - 内置函数:     {{timestamp()}} / {{uuid()}}
"""
import re
import logging
import random as _random
import uuid as _uuid
import time as _time
from typing import Any, Dict

from common.context import context
from common.faker import faker
from common.utils import TimeUtil, CryptoUtil
from common.exceptions import VariableNotResolvedError, ConfigError
from config import settings

logger = logging.getLogger(__name__)

# ============================================================
# 占位符匹配正则
# 匹配 {{xxx}} 或 {{xxx(...)}} 或 {{xxx('literal')}}
# ============================================================
_VAR_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.DOTALL)


class VariableResolver:
    """
    变量解析引擎（静态方法）
    """

    # ============================================================
    # 主入口：递归解析任意数据结构
    # ============================================================
    @classmethod
    def resolve(cls, data: Any) -> Any:
        """
        递归解析任意数据类型中的 {{...}} 占位符

        :param data: dict / list / str / 其他原生类型
        :return:     解析后的数据（结构相同，值已替换）

        示例：
            >>> VariableResolver.resolve({"token": "{{login_token}}"})
            {"token": "abc123"}
            >>> VariableResolver.resolve(["{{faker.name}}", "固定值"])
            ["张三", "固定值"]
        """
        if isinstance(data, dict):
            return {k: cls.resolve(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.resolve(item) for item in data]
        if isinstance(data, tuple):
            return tuple(cls.resolve(item) for item in data)
        if isinstance(data, str):
            return cls._resolve_string(data)
        # 其他类型（int/float/bool/None）原样返回
        return data

    # ============================================================
    # 字符串解析（可能含多个占位符）
    # ============================================================
    @classmethod
    def _resolve_string(cls, text: str) -> Any:
        """
        解析字符串中的 {{...}}

        情况：
            1. 整串仅有一个占位符 "{{order_id}}" → 返回原始类型（int / dict）
            2. 整串有多个占位符或与其他字符混合 → 返回字符串拼接结果
        """
        if not isinstance(text, str) or "{{" not in text:
            return text

        # 匹配所有占位符
        matches = list(_VAR_PATTERN.finditer(text))
        if not matches:
            return text

        # ---------- 情况 1: 整串仅一个占位符 ----------
        if len(matches) == 1 and matches[0].group(0) == text.strip():
            expr = matches[0].group(1).strip()
            return cls._evaluate(expr)

        # ---------- 情况 2: 有多个占位符或混合文本 → 字符串拼接 ----------
        def repl(match):
            expr = match.group(1).strip()
            try:
                value = cls._evaluate(expr)
            except Exception as e:
                logger.warning(f"[Resolver] 占位符 {match.group(0)} 解析失败: {e}")
                return match.group(0)  # 保留原文
            if value is None:
                return ""
            return str(value)

        return _VAR_PATTERN.sub(repl, text)

    # ============================================================
    # 表达式求值（核心路由）
    # ============================================================
    @classmethod
    def _evaluate(cls, expr: str) -> Any:
        """
        计算单个表达式的值

        :param expr: 表达式内容（不含 {{ }}）
        """
        if not expr:
            raise VariableNotResolvedError("空表达式")

        # 处理函数调用：xxx(...) 或 xxx
        name, args = cls._parse_expression(expr)

        # ---------- 1. 命名空间路由 ----------
        if name.startswith("env."):
            return cls._eval_env(name[4:])

        if name.startswith("context."):
            return cls._eval_context(name[8:])

        if name.startswith("faker."):
            return cls._eval_faker(name[6:], args)

        if name.startswith("time."):
            return cls._eval_time(name[5:], args)

        if name.startswith("crypto."):
            return cls._eval_crypto(name[7:], args)

        if name.startswith("random."):
            return cls._eval_random(name[7:], args)

        if name.startswith("jsonpath."):
            return cls._eval_jsonpath(name[9:], args)

        # ---------- 2. 无命名空间：内置函数 ----------
        builtin = cls._eval_builtin(name, args)
        if builtin is not _NOT_FOUND:
            return builtin

        # ---------- 3. 无命名空间：上下文变量（如 {{token}}）----------
        return cls._eval_context(name)

    # ============================================================
    # 表达式解析：区分 "xxx" 与 "xxx(args)"
    # ============================================================
    @staticmethod
    def _parse_expression(expr: str):
        """
        解析表达式，返回 (name, args_list)

        示例：
            "time.now_str"                  → ("time.now_str", [])
            "time.offset_days(30)"          → ("time.offset_days", [30])
            "crypto.md5('Test@123')"        → ("crypto.md5", ["Test@123"])
            "random.int(1, 100)"            → ("random.int", [1, 100])
        """
        expr = expr.strip()

        # 无函数调用
        if "(" not in expr:
            return expr, []

        # 有函数调用
        try:
            name, args_str = expr.split("(", 1)
            if not args_str.endswith(")"):
                raise ValueError(f"括号不匹配: {expr}")
            args_str = args_str[:-1].strip()

            args = VariableResolver._parse_args(args_str)
            return name.strip(), args
        except ValueError as e:
            raise VariableNotResolvedError(
                f"表达式解析失败: {expr} - {e}"
            ) from e

    @staticmethod
    def _parse_args(args_str: str) -> list:
        """
        解析函数参数列表

        示例：
            ""                       → []
            "30"                     → [30]
            "'Test@123'"             → ["Test@123"]
            "1, 100"                 → [1, 100]
            "'a', 'b', 3"            → ["a", "b", 3]
        """
        if not args_str:
            return []

        # 简单解析：按逗号分割，处理引号内的逗号
        args = []
        current = []
        in_quotes = False
        quote_char = None

        for ch in args_str:
            if ch in ("'", '"') and not in_quotes:
                in_quotes = True
                quote_char = ch
                current.append(ch)
            elif ch == quote_char and in_quotes:
                in_quotes = False
                current.append(ch)
            elif ch == "," and not in_quotes:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)

        if current:
            args.append("".join(current).strip())

        # 类型转换
        return [VariableResolver._cast_arg(a) for a in args]

    @staticmethod
    def _cast_arg(arg: str) -> Any:
        """将字符串参数转为合适的 Python 类型"""
        arg = arg.strip()
        if not arg:
            return ""

        # 字符串
        if (arg.startswith("'") and arg.endswith("'")) or \
                (arg.startswith('"') and arg.endswith('"')):
            return arg[1:-1]

        # 布尔
        lower = arg.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        if lower == "none" or lower == "null":
            return None

        # 整数
        try:
            return int(arg)
        except ValueError:
            pass

        # 浮点
        try:
            return float(arg)
        except ValueError:
            pass

        return arg

    # ============================================================
    # 各命名空间实现
    # ============================================================
    @staticmethod
    def _eval_env(path: str) -> Any:
        """
        环境变量：{{env.api.base_url}}
        """
        value = settings.get(path)
        if value is None:
            raise VariableNotResolvedError(
                f"环境配置不存在: env.{path}",
                available_keys=list(settings.to_dict().keys()),
            )
        return value

    @staticmethod
    def _eval_context(key: str) -> Any:
        """
        上下文变量：{{token}}
        优先读用例级，再读全局
        """
        value = context.get(key, scope="auto")
        if value is None:
            # 抛异常还是返回 None？
            # 策略：明确抛异常，避免静默打空值
            raise VariableNotResolvedError(
                f"上下文变量未定义: {key}",
                available_keys=list(context.get_all().keys()),
            )
        return value

    @staticmethod
    def _eval_faker(method: str, args: list) -> Any:
        """
        Faker：{{faker.name}} / {{faker.unique_phone}}
        """
        try:
            return faker.call(method, *args)
        except AttributeError as e:
            raise VariableNotResolvedError(
                f"Faker 方法不存在: {method} - {e}"
            ) from e

    @staticmethod
    def _eval_time(method: str, args: list) -> Any:
        """
        时间：{{time.now_str}} / {{time.offset_days(30)}}
        """
        try:
            return TimeUtil.call(method, *args)
        except AttributeError as e:
            raise VariableNotResolvedError(
                f"TimeUtil 方法不存在: {method}"
            ) from e

    @staticmethod
    def _eval_crypto(method: str, args: list) -> Any:
        """
        加密：{{crypto.md5('Test@123')}}
        """
        try:
            return CryptoUtil.call(method, *args)
        except AttributeError as e:
            raise VariableNotResolvedError(
                f"CryptoUtil 方法不存在: {method}"
            ) from e

    @staticmethod
    def _eval_random(method: str, args: list) -> Any:
        """
        随机：{{random.int(1, 100)}}
        """
        if method == "int":
            min_val = args[0] if len(args) > 0 else 1
            max_val = args[1] if len(args) > 1 else 100
            return _random.randint(int(min_val), int(max_val))
        if method == "choice":
            return _random.choice(args)
        if method == "str":
            length = int(args[0]) if args else 8
            return _random.choices(
                "abcdefghijklmnopqrstuvwxyz0123456789", k=length
            )
        raise VariableNotResolvedError(
            f"不支持的 random 方法: {method}（支持 int / choice / str）"
        )

    @staticmethod
    def _eval_jsonpath(method: str, args: list) -> Any:
        """
        JSONPath：{{jsonpath.extract(last_json, '$.token')}}
        """
        from common.utils import JsonPathUtil
        try:
            return JsonPathUtil.call(method, *args)
        except AttributeError as e:
            raise VariableNotResolvedError(
                f"JsonPathUtil 方法不存在: {method}"
            ) from e

    @staticmethod
    def _eval_builtin(name: str, args: list) -> Any:
        """
        内置函数：timestamp() / uuid() / today()
        """
        if name == "timestamp":
            return int(_time.time())
        if name == "timestamp_ms":
            return int(_time.time() * 1000)
        if name == "uuid":
            return _uuid.uuid4().hex
        if name == "today":
            return TimeUtil.today_str()
        return _NOT_FOUND


# ============================================================
# 内部哨兵（区分"未找到"与"值为 None"）
# ============================================================
class _NotFound:
    pass


_NOT_FOUND = _NotFound()