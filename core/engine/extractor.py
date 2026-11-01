# -*- coding: utf-8 -*-
"""
core/engine/extractor.py
------------------------
响应数据提取器（Extractor）

职责：
    1. 从 HTTP 响应中按 JSONPath、正则、Header 提取数据
    2. 支持嵌套提取（如从数组提取后给多个变量赋值）
    3. 提取结果自动存入 Context（供后续步骤引用）
    4. 支持提取失败时的默认值或失败策略

YAML 声明格式：
    extract:
      # 简写：默认从 JSON 中提取
      order_no: "$.orderNumber"

      # 完整写法：指定提取源
      token:
        source: "json"           # json / regex / header
        path: "$.token"

      phone:
        source: "regex"
        pattern: '"phone":\\s*"(\\d+)"'
        group: 1

      session_id:
        source: "header"
        name: "Set-Cookie"
        pattern: "JSESSIONID=([^;]+)"
        group: 1
"""
import logging
import re
from typing import Any, Dict, Optional

from common.context import context
from common.utils import JsonPathUtil
from common.exceptions import ConfigError

logger = logging.getLogger(__name__)


class Extractor:
    """
    响应数据提取器（静态方法）
    """

    # ============================================================
    # 主入口
    # ============================================================
    @classmethod
    def extract(cls, response: Any, rules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        根据 YAML 规则提取数据并存入 Context

        :param response: HttpResponse 对象
        :param rules:    YAML 中的 extract 节点
        :return:         提取结果字典（同时已存入 Context）

        示例：
            rules = {"order_no": "$.orderNumber"}
            result = Extractor.extract(response, rules)
            # result == {"order_no": "DD123456"}
        """
        if not rules:
            return {}

        if response is None:
            logger.warning("[Extractor] 响应为空，跳过提取")
            return {}

        result = {}

        for var_name, rule in rules.items():
            try:
                value = cls._extract_one(response, rule)
            except Exception as e:
                logger.error(
                    f"[Extractor] 提取变量 '{var_name}' 失败: {e}",
                    exc_info=True,
                )
                raise ConfigError(
                    f"提取变量 '{var_name}' 失败: {e}",
                    detail={"var_name": var_name, "rule": rule},
                ) from e

            if value is None:
                logger.warning(
                    f"[Extractor] 变量 '{var_name}' 提取结果为 None（规则: {rule}）"
                )
            else:
                result[var_name] = value
                # 存入上下文
                context.set(var_name, value, scope="case")
                logger.debug(
                    f"[Extractor] 提取 '{var_name}' = {_truncate(value)}"
                )

        return result

    # ============================================================
    # 单个变量的提取
    # ============================================================
    @classmethod
    def _extract_one(cls, response: Any, rule: Any) -> Any:
        """
        提取单个变量

        :param response: HttpResponse 对象
        :param rule:     提取规则（字符串 = JSONPath 简写；字典 = 完整写法）
        """
        # ---------- 简写：仅传 JSONPath ----------
        if isinstance(rule, str):
            # 纯占位符表达式（如 "{{faker.x}}" / "{{time.now}}"）：
            # 表示"直接使用该表达式的解析值"，而非从响应中抽取
            if _is_pure_placeholder(rule):
                return _resolve_placeholder(rule)
            resp_json = cls._get_json(response)
            return JsonPathUtil.extract(resp_json, rule, default=None)

        # ---------- 完整写法 ----------
        if isinstance(rule, dict):
            source = rule.get("source", "json").lower()

            if source == "json":
                path = rule.get("path") or rule.get("jsonpath")
                if not path:
                    raise ConfigError(f"json 提取缺少 path 参数: {rule}")
                resp_json = cls._get_json(response)
                return JsonPathUtil.extract(resp_json, path, default=None)

            elif source == "regex":
                return cls._extract_by_regex(response, rule)

            elif source == "header":
                return cls._extract_by_header(response, rule)

            elif source == "cookie":
                return cls._extract_by_cookie(response, rule)

            else:
                raise ConfigError(
                    f"不支持的提取源: {source}（可选 json/regex/header/cookie）"
                )

        raise ConfigError(
            f"extract 规则格式错误: {rule}（应为字符串或字典）"
        )

    # ============================================================
    # 不同提取源的实现
    # ============================================================
    @staticmethod
    def _extract_by_regex(response: Any, rule: Dict[str, Any]) -> Optional[str]:
        """
        从响应正文中用正则提取

        rule: {
            "source": "regex",
            "pattern": "...",
            "group": 1,        # 可选，默认 1
            "target": "text"   # 可选，text / json_text
        }
        """
        pattern = rule.get("pattern")
        if not pattern:
            raise ConfigError(f"regex 提取缺少 pattern: {rule}")

        group = rule.get("group", 1)
        target = rule.get("target", "text")

        if target == "text":
            text = response.text or ""
        else:
            text = _json_to_text(response)

        try:
            match = re.search(pattern, text, re.DOTALL)
        except re.error as e:
            raise ConfigError(f"正则表达式不合法: {pattern} - {e}")

        if not match:
            return None

        try:
            return match.group(group)
        except IndexError:
            raise ConfigError(
                f"正则分组 {group} 不存在（pattern: {pattern}）"
            )

    @staticmethod
    def _extract_by_header(response: Any, rule: Dict[str, Any]) -> Optional[str]:
        """
        从响应 Header 中提取

        rule: {
            "source": "header",
            "name": "Content-Type",
            "pattern": "...",   # 可选，正则提取
            "group": 1
        }
        """
        name = rule.get("name")
        if not name:
            raise ConfigError(f"header 提取缺少 name: {rule}")

        value = response.headers.get(name)
        if value is None:
            return None

        # 有 pattern 时用正则再提一次
        pattern = rule.get("pattern")
        if pattern:
            try:
                match = re.search(pattern, value)
            except re.error as e:
                raise ConfigError(f"正则表达式不合法: {pattern} - {e}")
            if not match:
                return None
            return match.group(rule.get("group", 1))

        return value

    @staticmethod
    def _extract_by_cookie(response: Any, rule: Dict[str, Any]) -> Optional[str]:
        """
        从 Cookie 中提取

        rule: {
            "source": "cookie",
            "name": "JSESSIONID"
        }
        """
        name = rule.get("name")
        if not name:
            raise ConfigError(f"cookie 提取缺少 name: {rule}")

        # 优先从 session.cookies 取
        cookies = getattr(response, "cookies", None)
        if cookies is not None:
            value = cookies.get(name)
            if value:
                return value

        # 从 Set-Cookie header 中解析
        set_cookie = response.headers.get("Set-Cookie", "")
        if set_cookie:
            pattern = rf"{re.escape(name)}=([^;]+)"
            match = re.search(pattern, set_cookie)
            if match:
                return match.group(1)

        return None

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _get_json(response: Any) -> Any:
        """安全获取响应 JSON"""
        try:
            return response.json()
        except Exception:
            return None


# ============================================================
# 内部辅助函数
# ============================================================
def _truncate(value: Any, max_len: int = 100) -> Any:
    """截断过长的值用于日志"""
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "..."


def _json_to_text(response: Any) -> str:
    """将 JSON 响应转为文本（用于正则提取）"""
    import json
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return response.text or ""


# ============================================================
# 占位符辅助（支持 extract 直接取值）
# ============================================================
import re as _re
_PURE_PLACEHOLDER_RE = _re.compile(r"^\s*\{\{.+\}\}\s*$")


def _is_pure_placeholder(value: str) -> bool:
    """判断字符串是否为"纯占位符"（整串就是一个 {{...}}）"""
    return bool(_PURE_PLACEHOLDER_RE.match(value))


def _resolve_placeholder(value):
    """解析纯占位符表达式，返回解析后的真实值（保留原始类型）"""
    try:
        from core.engine.variable_resolver import VariableResolver
        return VariableResolver.resolve(value)
    except Exception:
        return None
