# -*- coding: utf-8 -*-
"""
core/assertions/assert_factory.py
---------------------------------
断言工厂（统一分发引擎）

职责：
    1. 对外提供唯一入口 `validate(response, validators)`
    2. 根据 YAML 的 validate 列表，逐条路由到对应断言器
    3. 聚合断言结果，失败时抛出统一异常
    4. 支持自定义断言注册（扩展友好）
    5. 支持单条断言失败后继续执行（可选）
    6. 特殊处理：status_code 直接从 response 取，不走 JSON

设计原则：
    - 单一职责：只负责"分发"，不实现具体断言逻辑
    - 开闭原则：新增断言类型时，只扩展路由表
    - 快速失败：默认遇到首个失败即抛出，便于定位
    - 详细日志：每条断言执行前后都有日志，便于排查
"""
import logging
from typing import Any, Callable, Dict, List, Optional

# ★ 关键：用 ApiAssertionError 别名，避免与内置 AssertionError 冲突
from common.exceptions import ApiAssertionError as AssertionError, API_TESTException
from common.context import context

from .base_assert import BaseAssert
from .smart_assert import SmartAssert
from .schema_assert import SchemaAssert
from .db_assert import DBAssert
from .snapshot_assert import SnapshotAssert
from .perf_assert import PerfAssert

logger = logging.getLogger(__name__)


# ============================================================
# eq 断言的智能分发：status_code 走 response，其他走 resp_json
# ============================================================
def _dispatch_eq(response: Any, resp_json: Any, path: str, expected: Any) -> None:
    """
    eq 断言分发器

    - path == "status_code" → 从 response.status_code 取值
    - 其他 path             → 从 resp_json 用 JSONPath 取值
    """
    if path == "status_code":
        actual = getattr(response, "status_code", None)
        if actual != expected:
            raise AssertionError.from_eq("status_code", expected, actual)
        return

    BaseAssert.assert_eq(resp_json, path, expected)


def _dispatch_contains(response: Any, resp_json: Any, path: str, substring: Any) -> None:
    """
    contains 断言分发器

    - path == "status_code" → 通常不会这么写，也支持
    - 其他 path             → 走 BaseAssert
    """
    if path == "status_code":
        actual = str(getattr(response, "status_code", ""))
        if str(substring) not in actual:
            raise AssertionError.from_contains("status_code", substring, actual)
        return

    BaseAssert.assert_contains(resp_json, path, substring)


def _dispatch_not_eq(response: Any, resp_json: Any, path: str, expected: Any) -> None:
    """
    not_eq 断言分发器

    - path == "status_code" → 从 response.status_code 取值比较
    - 其他 path             → 从 resp_json 用 JSONPath 取值比较
    """
    if path == "status_code":
        actual = getattr(response, "status_code", None)
        if actual == expected:
            raise AssertionError(
                f"status_code 期望不等于 {expected}，实际相等",
                path="status_code", expected=f"!= {expected}", actual=actual,
            )
        return

    BaseAssert.assert_not_eq(resp_json, path, expected)


# ============================================================
# 断言工厂
# ============================================================
class AssertFactory:
    """
    断言工厂（路由分发器）

    用法：
        AssertFactory.validate(response, [
            {"eq": ["status_code", 200]},
            {"eq": ["$.error_code", "0000"]},
            {"max_time": 500},
            {"schema": "order_response.json"},
        ])
    """

    # ============================================================
    # 路由表：断言类型 → 处理函数
    # 处理函数签名：(response, resp_json, args) -> None
    # ============================================================
    _routes: Dict[str, Callable] = {
        # ---------- 基础断言 ----------
        "status_code":  lambda r, j, a: BaseAssert.assert_status_code(r, a[0]),
        "eq":           lambda r, j, a: _dispatch_eq(r, j, a[0], a[1]),
        "contains":     lambda r, j, a: _dispatch_contains(r, j, a[0], a[1]),
        "exists":       lambda r, j, a: BaseAssert.assert_exists(j, a[0]),
        "not_contains": lambda r, j, a: BaseAssert.assert_not_contains(j, a[0], a[1]),
        "not_eq":       lambda r, j, a: _dispatch_not_eq(r, j, a[0], a[1]),

        # ---------- 智能断言 ----------
        "regex":        lambda r, j, a: SmartAssert.assert_regex(j, a[0], a[1]),
        "type":         lambda r, j, a: SmartAssert.assert_type(j, a[0], a[1]),
        "not_null":     lambda r, j, a: SmartAssert.assert_not_null(j, a[0]),
        "in":           lambda r, j, a: SmartAssert.assert_in(j, a[0], a[1]),
        "length":       lambda r, j, a: SmartAssert.assert_length(j, a[0], a[1]),

        # ---------- 契约校验 ----------
        "schema":       lambda r, j, a: SchemaAssert.assert_schema(j, a[0]),

        # ---------- 数据库断言 ----------
        "db_eq":        lambda r, j, a: DBAssert.assert_db_eq(a[0], a[1]),
        "db_exists":    lambda r, j, a: DBAssert.assert_db_exists(a[0]),

        # ---------- 快照对比 ----------
        "snapshot":     lambda r, j, a: SnapshotAssert.assert_snapshot(j, a[0]),

        # ---------- 性能断言 ----------
        "max_time":     lambda r, j, a: PerfAssert.assert_max_time(r, a[0]),
        "p95_time":     lambda r, j, a: PerfAssert.assert_p95_time(r, a[0]),
    }

    # ============================================================
    # 主入口
    # ============================================================
    @classmethod
    def validate(
        cls,
        response: Any,
        validators: List[Dict[str, Any]],
        *,
        stop_on_first_failure: bool = True,
    ) -> None:
        """
        批量执行断言

        :param response:              HttpResponse 对象
        :param validators:            YAML 的 validate 节点解析后的列表
        :param stop_on_first_failure: 是否遇到首个失败即抛出（默认 True）

        :raises AssertionError: 任一断言失败（首个失败）
        :raises ValueError:     YAML 写错时（未知断言类型、参数格式错误）
        """
        if not validators:
            logger.debug("[Assert] 无断言需要执行")
            return

        # 解析响应 JSON（供需要 JSON 内容的断言使用）
        resp_json = cls._safe_parse_json(response)

        # 逐个执行断言
        failures: List = []
        total = len(validators)

        for index, validator in enumerate(validators, start=1):
            if not isinstance(validator, dict):
                raise ValueError(
                    f"validate 第 {index} 项格式错误，应为 dict，实际: {type(validator)}"
                )

            for assert_type, args in validator.items():
                cls._execute_one(
                    index, total, assert_type, args,
                    response, resp_json,
                    failures, stop_on_first_failure,
                )

        # 汇总结果
        if failures:
            if not stop_on_first_failure:
                summary = "; ".join(str(f) for f in failures)
                raise AssertionError(
                    f"共 {len(failures)} 项断言失败: {summary}",
                    detail={"failures": [f.to_dict() for f in failures]},
                )
        else:
            logger.info(f"[Assert] ✅ 全部 {total} 项断言通过")

    # ============================================================
    # 单条断言执行
    # ============================================================
    @classmethod
    def _execute_one(
        cls,
        index: int,
        total: int,
        assert_type: str,
        args: Any,
        response: Any,
        resp_json: Any,
        failures: List,
        stop_on_first_failure: bool,
    ) -> None:
        """执行单个断言"""
        # 1. 检查断言类型是否支持
        if assert_type not in cls._routes:
            raise ValueError(
                f"不支持的断言类型: '{assert_type}'，"
                f"可用类型: {list(cls._routes.keys())}"
            )

        # 2. 参数规整（统一转 list，方便解包）
        args_list = args if isinstance(args, list) else [args]

        # 3. 获取处理函数
        handler = cls._routes[assert_type]

        # 4. 执行断言
        try:
            logger.debug(
                f"[Assert] [{index}/{total}] {assert_type}({args_list})"
            )
            handler(response, resp_json, args_list)

        except AssertionError as e:
            # 业务断言失败（Failed）
            e.detail = e.detail or {}
            e.detail["assert_type"] = assert_type
            e.detail["args"] = args_list
            e.detail["index"] = f"{index}/{total}"
            logger.warning(
                f"[Assert] ❌ [{index}/{total}] {assert_type} 失败: {e.message}"
            )
            failures.append(e)
            if stop_on_first_failure:
                raise

        except API_TESTException as e:
            # 框架侧/环境侧异常（Broken），直接抛
            logger.error(f"[Assert] 💥 {assert_type} 执行异常: {e}")
            raise

        except Exception as e:
            # 未预期异常，包装成 AssertionError
            logger.error(
                f"[Assert] 💥 {assert_type} 内部错误: {e}", exc_info=True
            )
            wrapped = AssertionError(
                f"断言 {assert_type} 执行异常: {e}",
                detail={"assert_type": assert_type, "original": str(e)},
            )
            failures.append(wrapped)
            if stop_on_first_failure:
                raise wrapped

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _safe_parse_json(response: Any) -> Optional[Any]:
        """安全地解析响应 JSON"""
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            return None

    # ============================================================
    # 扩展能力
    # ============================================================
    @classmethod
    def register(cls, assert_type: str, handler: Callable) -> None:
        """
        注册自定义断言

        handler 签名：(response, resp_json, args_list) -> None
        """
        if assert_type in cls._routes:
            logger.warning(f"[Assert] 断言类型 '{assert_type}' 已存在，将被覆盖")
        cls._routes[assert_type] = handler
        logger.info(f"[Assert] 已注册自定义断言: {assert_type}")

    @classmethod
    def unregister(cls, assert_type: str) -> bool:
        """移除某个断言类型"""
        if assert_type in cls._routes:
            del cls._routes[assert_type]
            return True
        return False

    @classmethod
    def supported_types(cls) -> List[str]:
        """列出所有支持的断言类型"""
        return sorted(cls._routes.keys())