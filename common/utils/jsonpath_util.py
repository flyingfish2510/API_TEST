# -*- coding: utf-8 -*-
"""
common/utils/jsonpath_util.py
-----------------------------
JSONPath 提取工具类（JsonPathUtil）

支持能力：
    1. 单值提取（extract）
    2. 多值提取（extract_all）
    3. 是否存在（exists）
    4. 断言匹配（match）
    5. 容错提取（extract_safe，找不到返回默认值）

依赖：
    pip install jsonpath-ng
"""
import logging
from typing import Any, List, Optional, Union

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

logger = logging.getLogger(__name__)


class JsonPathUtil:
    """JSONPath 提取工具类（全静态方法）"""

    # ============================================================
    # 1. 单值提取
    # ============================================================
    @staticmethod
    def extract(data: Any, path: str, default: Any = None) -> Any:
        """
        提取单个值（取第一个匹配项）

        :param data:    待提取的数据（dict / list）
        :param path:    JSONPath 表达式（如 $.data.token）
        :param default: 未匹配时的返回值
        :return:        匹配的第一个值，未匹配返回 default

        示例：
            >>> JsonPathUtil.extract({"data": {"token": "abc123"}}, "$.data.token")
            'abc123'
            >>> JsonPathUtil.extract({"code": 200}, "$.data.token", default=None)
            None
        """
        if data is None:
            return default

        try:
            expr = jsonpath_parse(path)
        except JsonPathParserError as e:
            raise ValueError(f"JSONPath 语法错误: {path} - {e}") from e

        matches = expr.find(data)
        if not matches:
            logger.debug(f"[JsonPath] 未匹配: {path} (data={str(data)[:100]})")
            return default

        value = matches[0].value
        logger.debug(f"[JsonPath] 提取 {path} = {str(value)[:100]}")
        return value

    @staticmethod
    def extract_safe(data: Any, path: str, default: Any = "") -> Any:
        """
        容错提取（永不抛异常，用于非关键字段）
        """
        try:
            return JsonPathUtil.extract(data, path, default=default)
        except Exception as e:
            logger.warning(f"[JsonPath] 提取失败 {path}: {e}，返回默认值 {default}")
            return default

    # ============================================================
    # 2. 多值提取
    # ============================================================
    @staticmethod
    def extract_all(data: Any, path: str) -> List[Any]:
        """
        提取所有匹配的值（返回列表）

        示例（针对文档中的 cartList / records 数组）：
            >>> data = {"cartList": [{"id": 1}, {"id": 2}]}
            >>> JsonPathUtil.extract_all(data, "$.cartList[*].id")
            [1, 2]
        """
        if data is None:
            return []

        try:
            expr = jsonpath_parse(path)
        except JsonPathParserError as e:
            raise ValueError(f"JSONPath 语法错误: {path} - {e}") from e

        matches = expr.find(data)
        result = [m.value for m in matches]
        logger.debug(f"[JsonPath] 提取全部 {path}，共 {len(result)} 条")
        return result

    # ============================================================
    # 3. 存在性判断
    # ============================================================
    @staticmethod
    def exists(data: Any, path: str) -> bool:
        """
        判断 JSONPath 是否能匹配到值

        示例：
            >>> JsonPathUtil.exists({"data": {"token": "abc"}}, "$.data.token")
            True
            >>> JsonPathUtil.exists({"data": {}}, "$.data.token")
            False
        """
        if data is None:
            return False
        try:
            expr = jsonpath_parse(path)
            return bool(expr.find(data))
        except Exception:
            return False

    # ============================================================
    # 4. 断言匹配
    # ============================================================
    @staticmethod
    def match(data: Any, path: str, expected: Any) -> bool:
        """
        判断 JSONPath 提取的值是否等于 expected
        用于断言引擎的 eq 断言

        示例：
            >>> JsonPathUtil.match({"error_code": "0000"}, "$.error_code", "0000")
            True
        """
        actual = JsonPathUtil.extract(data, path, default=object())
        if actual is object():
            return False
        return actual == expected

    @staticmethod
    def contains(data: Any, path: str, substring: Any) -> bool:
        """
        判断 JSONPath 提取的值是否包含 substring
        用于断言引擎的 contains 断言（文档中的 msg 字段常用）

        示例：
            >>> JsonPathUtil.contains({"msg": "登录成功"}, "$.msg", "成功")
            True
        """
        actual = JsonPathUtil.extract(data, path, default=None)
        if actual is None:
            return False
        return str(substring) in str(actual)

    # ============================================================
    # 5. 便捷批量提取
    # ============================================================
    @staticmethod
    def extract_dict(data: Any, mapping: dict) -> dict:
        """
        批量提取（用于 extract 节点批量定义）

        :param data:    待提取数据
        :param mapping: {变量名: JSONPath} 映射
        :return:        提取后的字典

        示例（对应 YAML 中的 extract 节点）：
            >>> resp = {"orderNumber": "DD123", "userId": 100}
            >>> JsonPathUtil.extract_dict(resp, {
            ...     "order_no": "$.orderNumber",
            ...     "user_id": "$.userId"
            ... })
            {'order_no': 'DD123', 'user_id': 100}
        """
        result = {}
        for var_name, path in mapping.items():
            value = JsonPathUtil.extract(data, path, default=None)
            if value is None:
                logger.warning(f"[JsonPath] 提取 {var_name} 失败，路径 {path} 无匹配")
            result[var_name] = value
        return result

    # ============================================================
    # 6. 通用调用（供 VariableResolver 动态调用）
    # ============================================================
    @classmethod
    def call(cls, method: str, *args, **kwargs):
        """
        动态调用入口：
            {{jsonpath.extract(last_json, '$.token')}}
        """
        if not hasattr(cls, method):
            raise AttributeError(f"JsonPathUtil 无此方法: {method}")
        return getattr(cls, method)(*args, **kwargs)