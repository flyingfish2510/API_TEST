# -*- coding: utf-8 -*-
"""
core/mock/mock_loader.py
------------------------
Mock 规则加载器（MockLoader）

职责：
    1. 从 data/mocks/ 加载 JSON 格式的 Mock 规则文件
    2. 解析规则（支持路径、方法、正则、延迟、状态码配置）
    3. 将规则转为 MockRule 对象供 MockServer 匹配
    4. 支持规则文件的缓存，避免重复 IO
"""
import os
import re
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.utils import FileUtil
from common.exceptions import MockRuleNotFoundError, ConfigError

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型：Mock 规则
# ============================================================
@dataclass
class MockRule:
    """
    单条 Mock 规则

    示例：
        MockRule(
            method="POST",
            path="/pay/callback",
            status=200,
            headers={"Content-Type": "application/json"},
            body={"return_code": "SUCCESS"},
            delay=0,
        )
    """
    # 匹配条件
    method: str = "*"  # HTTP 方法，* 表示匹配全部
    path: str = "/"  # 路径，支持正则
    is_regex: bool = False  # 是否按正则匹配
    query_match: Dict[str, str] = field(default_factory=dict)  # Query 参数匹配（AND）
    body_match: Dict[str, Any] = field(default_factory=dict)  # Body 字段匹配（AND）

    # 响应配置
    status: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    delay: float = 0.0  # 响应延迟（秒），用于模拟超时

    # 命中次数限制（可选）
    max_hits: int = 0  # 0 表示不限制
    hit_count: int = 0  # 运行时的命中计数

    def matches(
            self,
            method: str,
            path: str,
            query: Dict[str, List[str]],
            body: Dict[str, Any],
    ) -> bool:
        """判断当前请求是否匹配本规则"""
        # 1. 命中次数限制
        if self.max_hits > 0 and self.hit_count >= self.max_hits:
            return False

        # 2. 方法匹配
        if self.method != "*" and self.method.upper() != method.upper():
            return False

        # 3. 路径匹配
        if self.is_regex:
            if not re.search(self.path, path):
                return False
        else:
            if self.path.rstrip("/") != path.rstrip("/"):
                return False

        # 4. Query 参数匹配
        for k, v in self.query_match.items():
            values = query.get(k, [])
            if not values or str(v) not in values:
                return False

        # 5. Body 字段匹配
        for k, v in self.body_match.items():
            if not isinstance(body, dict) or body.get(k) != v:
                return False

        return True

    def render_response(
            self,
            request_path: str,
            request_query: Dict[str, List[str]],
            request_body: Any,
    ) -> Dict[str, Any]:
        """
        渲染响应内容（处理动态变量）

        支持的内置变量：
            {{$uuid}}             生成随机 UUID
            {{$timestamp}}        生成当前时间戳
            {{$path}}             请求路径
            {{$query.xxx}}        请求的 Query 参数
            {{$body.xxx}}         请求的 Body 字段
        """
        context = {
            "uuid": lambda: uuid.uuid4().hex,
            "timestamp": lambda: int(__import__("time").time()),
            "path": lambda: request_path,
        }

        def replace_var(match):
            expr = match.group(1).strip()

            # {{$uuid}} / {{$timestamp}} / {{$path}}
            if expr.startswith("$"):
                name = expr[1:]
                if name.startswith("query."):
                    key = name[len("query."):]
                    values = request_query.get(key, [])
                    return values[0] if values else ""
                if name.startswith("body."):
                    key = name[len("body."):]
                    if isinstance(request_body, dict):
                        return str(request_body.get(key, ""))
                    return ""
                if name in context:
                    return str(context[name]())
                return ""

            return match.group(0)  # 未知变量保留原文

        # 递归处理 body 中的字符串
        def render_obj(obj):
            if isinstance(obj, str):
                return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_var, obj)
            if isinstance(obj, dict):
                return {k: render_obj(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [render_obj(item) for item in obj]
            return obj

        return {
            "status": self.status,
            "headers": dict(self.headers),
            "body": render_obj(self.body),
            "delay": self.delay,
        }


# ============================================================
# 规则加载器
# ============================================================
class MockLoader:
    """
    Mock 规则加载器（静态方法）
    """

    # 默认规则目录
    DEFAULT_DIR = "data/mocks"

    # 规则缓存：{file_path: [MockRule, ...]}
    _cache: Dict[str, List[MockRule]] = {}

    # ============================================================
    # 加载入口
    # ============================================================
    @classmethod
    def load(
            cls,
            rule_name: str,
            mock_dir: Optional[str] = None,
            force_reload: bool = False,
    ) -> List[MockRule]:
        """
        加载规则文件

        :param rule_name:    文件名（如 "payment_gateway.json"）或相对路径
        :param mock_dir:     规则目录（默认 data/mocks）
        :param force_reload: 是否跳过缓存
        :return:             MockRule 列表
        """
        mock_dir = mock_dir or cls.DEFAULT_DIR

        # 拼接完整路径
        if os.path.isabs(rule_name):
            full_path = rule_name
        else:
            full_path = os.path.join(mock_dir, rule_name)
            if not full_path.endswith(".json"):
                full_path += ".json"

        # 缓存命中
        if not force_reload and full_path in cls._cache:
            return cls._cache[full_path]

        # 文件校验
        if not os.path.exists(full_path):
            raise MockRuleNotFoundError(
                f"Mock 规则文件不存在: {full_path}"
            )

        # 加载 JSON
        try:
            raw_data = FileUtil.load_json(full_path)
        except Exception as e:
            raise ConfigError(f"Mock 规则文件解析失败: {full_path} - {e}") from e

        # 解析规则
        rules = cls._parse_rules(raw_data, full_path)

        # 缓存
        cls._cache[full_path] = rules
        logger.info(
            f"[MockLoader] 加载规则: {full_path} ({len(rules)} 条规则)"
        )
        return rules

    # ============================================================
    # 解析规则
    # ============================================================
    @classmethod
    def _parse_rules(cls, raw_data: Any, file_path: str) -> List[MockRule]:
        """解析 JSON 数据为 MockRule 列表"""
        if not isinstance(raw_data, dict):
            raise ConfigError(
                f"Mock 规则文件格式错误（根节点应为对象）: {file_path}"
            )

        rules_raw = raw_data.get("rules", [])
        if not isinstance(rules_raw, list):
            raise ConfigError(
                f"Mock 规则的 rules 字段必须是数组: {file_path}"
            )

        rules = []
        for idx, item in enumerate(rules_raw):
            try:
                rule = cls._parse_one_rule(item, idx, file_path)
                rules.append(rule)
            except Exception as e:
                raise ConfigError(
                    f"规则第 {idx + 1} 条解析失败: {e}\n文件: {file_path}"
                ) from e

        return rules

    @staticmethod
    def _parse_one_rule(item: Dict[str, Any], idx: int, file_path: str) -> MockRule:
        """解析单条规则"""
        if not isinstance(item, dict):
            raise ConfigError(f"规则格式错误（应为对象）: {item}")

        match_cfg = item.get("match", {})
        resp_cfg = item.get("response", {})
        if not isinstance(match_cfg, dict) or not isinstance(resp_cfg, dict):
            raise ConfigError(f"match / response 必须是对象: {item}")

        # 解析匹配条件
        method = str(match_cfg.get("method", "*")).upper()
        path = match_cfg.get("path", "/")
        is_regex = bool(match_cfg.get("regex", False))

        # 校验路径必填
        if not path:
            raise ConfigError(f"规则匹配缺少 path: 第 {idx + 1} 条")

        # 如果配置了 is_regex，预编译检查正则合法性
        if is_regex:
            try:
                re.compile(path)
            except re.error as e:
                raise ConfigError(
                    f"正则表达式不合法: {path} - {e}"
                )

        query_match = match_cfg.get("query", {}) or {}
        body_match = match_cfg.get("body", {}) or {}

        # 解析响应配置
        status = int(resp_cfg.get("status", 200))
        headers = resp_cfg.get("headers", {}) or {}
        body = resp_cfg.get("body")
        delay = float(resp_cfg.get("delay", 0.0))

        # 命中次数限制
        max_hits = int(item.get("max_hits", 0))

        return MockRule(
            method=method,
            path=path,
            is_regex=is_regex,
            query_match=query_match,
            body_match=body_match,
            status=status,
            headers=headers,
            body=body,
            delay=delay,
            max_hits=max_hits,
        )

    # ============================================================
    # 缓存管理
    # ============================================================
    @classmethod
    def clear_cache(cls) -> None:
        """清空缓存"""
        cls._cache.clear()
        logger.info("[MockLoader] 缓存已清空")