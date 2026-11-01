# -*- coding: utf-8 -*-
"""
core/assertions/base_assert.py
------------------------------
基础断言器（BaseAssert）

支持的断言：
    - assert_status_code:   HTTP 状态码
    - assert_eq:            JSONPath 精确匹配
    - assert_contains:      包含子串
    - assert_not_contains:  不包含子串
    - assert_exists:        字段存在
    - assert_header:        响应 Header 匹配（扩展）

设计原则：
    - 所有方法签名统一：接收 resp_json / response 与断言参数
    - 失败时抛出 common.exceptions.ApiAssertionError
    - 成功时返回 None，不产生副作用
"""
import logging
from typing import Any

from common.utils import JsonPathUtil
# ★ 关键：用 ApiAssertionError 别名，避免与内置 AssertionError 冲突
from common.exceptions import ApiAssertionError as AssertionError

logger = logging.getLogger(__name__)


# ============================================================
# 内部哨兵对象（区分"字段不存在"与"字段值为 None"）
# ============================================================
_SENTINEL = object()


class BaseAssert:
    """基础断言器（全静态方法）"""

    # ============================================================
    # 1. HTTP 状态码断言
    # ============================================================
    @staticmethod
    def assert_status_code(response: Any, expected: int) -> None:
        """
        断言 HTTP 状态码

        :param response: HttpResponse 对象
        :param expected: 期望的状态码（如 200）
        """
        if response is None:
            raise AssertionError("响应对象为空，无法断言状态码")

        actual = getattr(response, "status_code", None)
        if actual != expected:
            raise AssertionError(
                f"HTTP 状态码不符: 期望 {expected}，实际 {actual}",
                detail={
                    "expected": expected,
                    "actual": actual,
                    "url": getattr(response, "url", ""),
                },
            )

    # ============================================================
    # 2. JSON 相等断言
    # ============================================================
    @staticmethod
    def assert_eq(resp_json: Any, path: str, expected: Any) -> None:
        """
        断言 JSONPath 提取值 == 期望值

        :param resp_json: 响应 JSON
        :param path:      JSONPath（如 $.error_code）
        :param expected:  期望值

        注意：
            - 提取失败（字段不存在）会作为失败处理
            - 类型严格比较：1 != "1"
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={
                    "path": path,
                    "expected": expected,
                    "reason": "field_missing",
                },
            )

        if actual != expected:
            raise AssertionError.from_eq(path, expected, actual)

    # ============================================================
    # 2.1 JSON 不相等断言
    # ============================================================
    @staticmethod
    def assert_not_eq(resp_json: Any, path: str, expected: Any) -> None:
        """
        断言 JSONPath 提取值 != 期望值

        用途：验证异常场景下业务码不应等于成功值（如 msg_code != 200）

        注意：
            - 符号/类型严格比较：1 != "1"
            - 字段缺失视为失败（无法验证"不相等"）
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在，无法执行 not_eq 断言: {path}",
                detail={"path": path, "expected": f"!= {expected}", "reason": "field_missing"},
            )

        if actual == expected:
            raise AssertionError(
                f"{path} 期望不等于 {expected}，实际相等",
                path=path, expected=f"!= {expected}", actual=actual,
            )

    # ============================================================
    # 3. 包含断言
    # ============================================================
    @staticmethod
    def assert_contains(resp_json: Any, path: str, substring: Any) -> None:
        """
        断言 JSONPath 提取值包含子串

        :param resp_json: 响应 JSON
        :param path:      JSONPath
        :param substring: 期望包含的子串
        """
        actual = JsonPathUtil.extract(resp_json, path, default=_SENTINEL)

        if actual is _SENTINEL:
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path, "reason": "field_missing"},
            )

        if str(substring) not in str(actual):
            raise AssertionError.from_contains(path, substring, actual)

    # ============================================================
    # 4. 不包含断言
    # ============================================================
    @staticmethod
    def assert_not_contains(resp_json: Any, path: str, substring: Any) -> None:
        """
        断言 JSONPath 提取值不包含子串（用于验证错误信息不应出现）
        """
        actual = JsonPathUtil.extract(resp_json, path, default=None)

        if actual is not None and str(substring) in str(actual):
            raise AssertionError(
                f"{path} 不应包含 {substring}，实际: {actual}",
                detail={
                    "path": path,
                    "unexpected": substring,
                    "actual": actual,
                },
            )

    # ============================================================
    # 5. 存在性断言
    # ============================================================
    @staticmethod
    def assert_exists(resp_json: Any, path: str) -> None:
        """
        断言 JSONPath 能提取到值（字段存在）

        注意：值为 None 也算存在，字段路径匹配不到才失败
        """
        if not JsonPathUtil.exists(resp_json, path):
            raise AssertionError(
                f"字段不存在: {path}",
                detail={"path": path},
            )

    # ============================================================
    # 6. Header 断言（扩展）
    # ============================================================
    @staticmethod
    def assert_header(response: Any, header_name: str, expected: str) -> None:
        """
        断言响应 Header 的值

        :param response:    HttpResponse 对象
        :param header_name: Header 名称（不区分大小写）
        :param expected:    期望值
        """
        if response is None:
            raise AssertionError("响应对象为空，无法断言 Header")

        actual = response.headers.get(header_name)
        if actual != expected:
            raise AssertionError(
                f"Header '{header_name}' 不符: 期望 {expected}，实际 {actual}",
                detail={
                    "header": header_name,
                    "expected": expected,
                    "actual": actual,
                },
            )