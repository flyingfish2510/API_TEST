# -*- coding: utf-8 -*-
"""
core/assertions/schema_assert.py
--------------------------------
JSON Schema 契约断言器（SchemaAssert）

职责：
    1. 从 data/schemas/ 目录加载 JSON Schema
    2. 校验响应 JSON 是否符合契约
    3. 支持 $ref 引用、$defs 复用
    4. 详细输出每一条校验失败的路径与原因
    5. 支持部分字段跳过（ignore_fields）

依赖：
    - jsonschema>=4.19.0
    - common.utils.FileUtil

设计原则：
    - 契约优先：响应结构必须符合定义
    - 友好报错：失败时给出具体字段路径与错误原因
    - 缓存加载：同一 schema 只加载一次，提升性能
"""
import os
import logging
from typing import Any, Dict, List, Optional

from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError as JsonschemaError

from common.utils import FileUtil
# ★ 关键：使用 SchemaValidationError（继承 ApiAssertionError）
from common.exceptions import SchemaValidationError

logger = logging.getLogger(__name__)


class SchemaAssert:
    """
    JSON Schema 契约断言器（静态方法）
    """

    # Schema 缓存：{schema_path: schema_dict}
    _schema_cache: Dict[str, dict] = {}

    # 默认 Schema 目录
    DEFAULT_SCHEMA_DIR = "data/schemas"

    # ============================================================
    # 1. 加载 Schema
    # ============================================================
    @classmethod
    def load_schema(
        cls,
        schema_name: str,
        schema_dir: Optional[str] = None,
    ) -> dict:
        """
        加载 JSON Schema（带缓存）

        :param schema_name: Schema 文件名（如 "order_response.json"）或相对路径
        :param schema_dir:  Schema 目录（默认 data/schemas）
        :return:           Schema 字典
        :raises SchemaValidationError: Schema 文件不存在或格式错误
        """
        schema_dir = schema_dir or cls.DEFAULT_SCHEMA_DIR

        # 拼接完整路径（若 schema_name 已是绝对路径则直接使用）
        if os.path.isabs(schema_name):
            full_path = schema_name
        else:
            full_path = os.path.join(schema_dir, schema_name)
            # 自动补 .json 后缀
            if not full_path.endswith(".json"):
                full_path += ".json"

        # 缓存命中直接返回
        if full_path in cls._schema_cache:
            return cls._schema_cache[full_path]

        # 加载文件
        if not os.path.exists(full_path):
            raise SchemaValidationError(
                f"Schema 文件不存在: {full_path}",
                schema_name=schema_name,
            )

        try:
            schema = FileUtil.load_json(full_path)
        except Exception as e:
            raise SchemaValidationError(
                f"Schema 文件解析失败: {full_path} - {e}",
                schema_name=schema_name,
            )

        # 校验 Schema 本身是否合法
        try:
            Draft7Validator.check_schema(schema)
        except Exception as e:
            raise SchemaValidationError(
                f"Schema 语法不合法: {full_path} - {e}",
                schema_name=schema_name,
            )

        # 存入缓存
        cls._schema_cache[full_path] = schema
        logger.debug(f"[SchemaAssert] 已加载 Schema: {full_path}")
        return schema

    # ============================================================
    # 2. 核心断言
    # ============================================================
    @classmethod
    def assert_schema(
        cls,
        resp_json: Any,
        schema_name: str,
        *,
        schema_dir: Optional[str] = None,
        ignore_fields: Optional[List[str]] = None,
    ) -> None:
        """
        断言响应 JSON 符合 JSON Schema

        :param resp_json:     响应 JSON（dict / list）
        :param schema_name:   Schema 文件（如 "order_response.json"）
        :param schema_dir:    Schema 目录
        :param ignore_fields: 需要跳过的字段（顶层字段名列表，如 ["timestamp"]）

        :raises SchemaValidationError: 校验失败
        """
        if resp_json is None:
            raise SchemaValidationError(
                "响应 JSON 为空，无法校验契约",
                schema_name=schema_name,
            )

        # 加载 Schema
        schema = cls.load_schema(schema_name, schema_dir)

        # 处理需要忽略的字段（从数据中删除，避免校验）
        if ignore_fields:
            resp_json = cls._remove_fields(resp_json, ignore_fields)

        # 校验
        validator = Draft7Validator(schema)
        errors = sorted(validator.iter_errors(resp_json), key=lambda e: e.path)

        if not errors:
            logger.info(
                f"[SchemaAssert] ✅ 契约校验通过: {schema_name} "
                f"(数据路径数: {cls._count_paths(resp_json)})"
            )
            return

        # 构建详细错误列表
        error_details = cls._format_errors(errors)
        summary = cls._build_summary(error_details)

        raise SchemaValidationError(
            message=f"响应不符合契约 [{schema_name}]，共 {len(errors)} 处错误\n{summary}",
            schema_name=schema_name,
            errors=error_details,
        )

    # ============================================================
    # 3. 辅助方法
    # ============================================================
    @staticmethod
    def _format_errors(errors: List[JsonschemaError]) -> List[Dict[str, Any]]:
        """将 jsonschema 异常转为结构化 dict"""
        result = []
        for err in errors:
            path_str = ".".join(str(p) for p in err.absolute_path) or "$"
            result.append({
                "path": path_str,
                "message": err.message,
                "validator": err.validator,
                "validator_value": _safe_serialize(err.validator_value),
                "instance": _safe_serialize(err.instance),
            })
        return result

    @staticmethod
    def _build_summary(error_details: List[Dict[str, Any]]) -> str:
        """构建人类可读的错误摘要（前 5 条）"""
        lines = []
        for item in error_details[:5]:
            lines.append(
                f"  ✗ {item['path']}: {item['message']} "
                f"(规则: {item['validator']})"
            )
        if len(error_details) > 5:
            lines.append(f"  ... 还有 {len(error_details) - 5} 处错误")
        return "\n".join(lines)

    @staticmethod
    def _remove_fields(data: Any, fields: List[str]) -> Any:
        """
        递归删除顶层指定字段

        注意：仅删除顶层同名字段，深层同名字段不动
        """
        if not fields:
            return data

        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in fields}

        if isinstance(data, list):
            return [SchemaAssert._remove_fields(item, fields) for item in data]

        return data

    @staticmethod
    def _count_paths(data: Any) -> int:
        """统计 JSON 中的路径总数（用于日志）"""
        if isinstance(data, dict):
            return sum(1 + SchemaAssert._count_paths(v) for v in data.values())
        if isinstance(data, list):
            return sum(SchemaAssert._count_paths(item) for item in data)
        return 1

    # ============================================================
    # 4. 缓存管理
    # ============================================================
    @classmethod
    def clear_cache(cls) -> None:
        """清空 Schema 缓存（用于测试热更新场景）"""
        cls._schema_cache.clear()
        logger.info("[SchemaAssert] Schema 缓存已清空")

    @classmethod
    def list_loaded(cls) -> List[str]:
        """列出所有已加载的 Schema"""
        return list(cls._schema_cache.keys())


# ============================================================
# 内部辅助：安全序列化
# ============================================================
def _safe_serialize(obj: Any, max_len: int = 200) -> Any:
    """
    安全序列化：处理不可 JSON 序列化的对象，并截断过长的内容
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        s = str(obj)
        return s if len(s) <= max_len else s[:max_len] + "..."

    if isinstance(obj, (list, dict)):
        try:
            import json
            s = json.dumps(obj, ensure_ascii=False, default=str)
            return s if len(s) <= max_len else s[:max_len] + "..."
        except Exception:
            return repr(obj)[:max_len]

    return repr(obj)[:max_len]