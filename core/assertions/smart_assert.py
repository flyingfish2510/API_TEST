# -*- coding: utf-8 -*-
"""
core/assertions/smart_assert.py
-------------------------------
智能断言器（SmartAssert）

支持的断言：
    - assert_regex:    正则匹配（订单号、信用代码、手机号等）
    - assert_type:     类型校验（int/string/float/bool/array/object）
    - assert_not_null: 非空（None / "" / [] / {} 都视为空）
    - assert_in:       枚举值校验
    - assert_length:   长度校验（字符串长度 / 数组长度 / 对象字段数）

设计原则：
    - 语义清晰：正则、类型、枚举一览即懂
    - 报错精准：显示实际值、期望规则、字段路径
    - 兼容性强：智能处理数字/字符串、True/1 等边界
"""
import logging
import re
from typing import Any, List, Union

from common.utils import JsonPathUtil
# ★ 关键：用 ApiAssertionError 别名，避免与内置 AssertionError 冲突
from common.exceptions import ApiAssertionError as AssertionError

logger = logging.getLogger(__name__)


# ============================================================
# 内部哨兵（区分"字段不存在"与"值为 None"）
# ============================================================
_SENTINEL = object()


class SmartAssert:
    """智能断言器（全静态方法）"""

    # ============================================================
    # 1. 正则匹配
    # ============================================================
    @staticmethod
    def assert_regex(resp_json: Any, path: str, pattern: str) -> None:
        """
        断言字段值匹配正则表达式

        :param resp_json: 响应 JSON
        :param path:      JSONPath
        :param pattern:   正则表达式

        示例：
            - regex: ["$.orderNumber", "^DD\\d{17}$"]      # DD + 17位数字
            - regex: ["$.creditIdentifier", "^[0-9A-Z]{18}$"]  # 18位信用代码
            - regex: ["$.phone", "^1[3-9]\\d{9}$"]         # 手机号
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        # 数值/布尔自动转字符串匹配
        actual_str = str(actual) if not isinstance(actual, str) else actual

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            raise AssertionError(
                f"正则表达式不合法: {pattern} - {e}",
                detail={"pattern": pattern, "reason": "invalid_regex"},
            )

        if not compiled.search(actual_str):
            raise AssertionError(
                f"{path} 值 '{actual}' 不匹配正则 {pattern}",
                detail={
                    "path": path,
                    "pattern": pattern,
                    "actual": actual,
                },
            )

        logger.debug(f"[SmartAssert] ✅ regex 通过: {path} = {actual}")

    # ============================================================
    # 2. 类型校验
    # ============================================================
    @staticmethod
    def assert_type(resp_json: Any, path: str, expected_type: str) -> None:
        """
        断言字段类型

        :param expected_type: int / float / number / str/string / bool
                              / array/list / object/dict / null
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        type_lower = expected_type.lower()
        ok, actual_type_name = _check_type(actual, type_lower)

        if not ok:
            raise AssertionError(
                f"{path} 类型不符: 期望 {expected_type}，实际 {actual_type_name}（值: {_truncate(actual)}）",
                detail={
                    "path": path,
                    "expected_type": expected_type,
                    "actual_type": actual_type_name,
                    "actual_value": _truncate(actual),
                },
            )

        logger.debug(f"[SmartAssert] ✅ type 通过: {path} 是 {actual_type_name}")

    # ============================================================
    # 3. 非空断言
    # ============================================================
    @staticmethod
    def assert_not_null(resp_json: Any, path: str) -> None:
        """
        断言字段非空

        空值定义：
            - None
            - 空字符串 ""
            - 空列表 []
            - 空字典 {}
            - 纯空白字符串 "  "

        注意：
            0 / False 视为"有效值"，不算空
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        if _is_empty(actual):
            raise AssertionError(
                f"{path} 值为空: {_truncate(actual)}",
                detail={
                    "path": path,
                    "actual": _truncate(actual),
                    "reason": "empty_value",
                },
            )

        logger.debug(f"[SmartAssert] ✅ not_null 通过: {path} = {_truncate(actual)}")

    # ============================================================
    # 4. 枚举值校验
    # ============================================================
    @staticmethod
    def assert_in(resp_json: Any, path: str, allowed: List[Any]) -> None:
        """
        断言字段值在允许的枚举列表中

        :param allowed: 允许的值列表
        """
        if not isinstance(allowed, (list, tuple, set)):
            raise AssertionError(
                f"in 断言的允许值必须是列表，实际: {type(allowed)}",
                detail={"path": path, "allowed": allowed},
            )

        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        # 数值/字符串兼容比较（"200" vs 200）
        if actual not in allowed and str(actual) not in [str(a) for a in allowed]:
            raise AssertionError(
                f"{path} 值 '{actual}' 不在允许列表 {allowed}",
                detail={
                    "path": path,
                    "actual": actual,
                    "allowed": list(allowed),
                },
            )

        logger.debug(f"[SmartAssert] ✅ in 通过: {path} = {actual}")

    # ============================================================
    # 5. 长度校验
    # ============================================================
    @staticmethod
    def assert_length(
        resp_json: Any,
        path: str,
        expected: Union[int, dict],
    ) -> None:
        """
        断言字段长度

        :param expected:
            - 传 int: 精确长度
            - 传 dict: {"min": 1, "max": 100} 范围

        适用类型：
            - 字符串：字符数
            - 数组：元素个数
            - 字典：键数量
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        try:
            actual_len = len(actual)
        except TypeError:
            raise AssertionError(
                f"{path} 类型不支持 len() 操作: {type(actual).__name__}",
                detail={"path": path, "actual_type": type(actual).__name__},
            )

        # 精确长度
        if isinstance(expected, int):
            if actual_len != expected:
                raise AssertionError(
                    f"{path} 长度不符: 期望 {expected}，实际 {actual_len}",
                    detail={
                        "path": path,
                        "expected_len": expected,
                        "actual_len": actual_len,
                    },
                )
            logger.debug(f"[SmartAssert] ✅ length 通过: {path} = {actual_len}")
            return

        # 范围长度
        if isinstance(expected, dict):
            min_len = expected.get("min")
            max_len = expected.get("max")
            if min_len is not None and actual_len < min_len:
                raise AssertionError(
                    f"{path} 长度 {actual_len} < 最小值 {min_len}",
                    detail={"path": path, "actual_len": actual_len, "min": min_len},
                )
            if max_len is not None and actual_len > max_len:
                raise AssertionError(
                    f"{path} 长度 {actual_len} > 最大值 {max_len}",
                    detail={"path": path, "actual_len": actual_len, "max": max_len},
                )
            logger.debug(f"[SmartAssert] ✅ length 通过: {path} = {actual_len} (范围)")
            return

        raise AssertionError(
            f"length 断言参数必须是 int 或 dict，实际: {type(expected)}",
            detail={"path": path, "expected": expected},
        )


# ============================================================
# 内部辅助函数
# ============================================================
def _check_type(value: Any, type_name: str) -> tuple:
    """
    校验类型，返回 (是否通过, 实际类型名)
    """
    type_map = {
        "int": (int,),
        "integer": (int,),
        "float": (float,),
        "number": (int, float),        # 数值：int 或 float 都算
        "str": (str,),
        "string": (str,),
        "bool": (bool,),
        "boolean": (bool,),
        "array": (list,),
        "list": (list,),
        "object": (dict,),
        "dict": (dict,),
        "null": (type(None),),
        "none": (type(None),),
    }

    if type_name not in type_map:
        return False, f"unknown_type_{type_name}"

    expected_types = type_map[type_name]

    # bool 是 int 的子类，需单独排除
    if type_name in ("int", "integer") and isinstance(value, bool):
        return False, "bool"

    ok = isinstance(value, expected_types)
    actual_name = type(value).__name__
    return ok, actual_name


def _is_empty(value: Any) -> bool:
    """
    判断是否为"空值"

    - None → True
    - "" / "   " → True
    - [] / {} → True
    - 0 / False → False（视为有效值）
    """
    if value is None:
        return True

    if isinstance(value, str):
        return len(value.strip()) == 0

    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0

    return False


def _truncate(value: Any, max_len: int = 100) -> Any:
    """截断过长的值，用于错误信息"""
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return value if len(value) <= max_len else value[:max_len] + "..."
    if isinstance(value, (list, dict)):
        s = str(value)
        return s if len(s) <= max_len else s[:max_len] + "..."
    return value