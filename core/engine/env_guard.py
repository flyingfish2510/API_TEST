# -*- coding: utf-8 -*-
"""
core/engine/env_guard.py
------------------------
环境安全守卫（EnvGuard）

职责：
    1. 生产环境强制只读（拦截所有写操作）
    2. 拦截显式黑名单 URL（高危接口）
    3. 拦截显式黑名单方法（PUT / DELETE / PATCH）
    4. 只读白名单机制（允许特定只读类 POST）
    5. 高危操作审计日志（记录谁在什么时间调了什么接口）

安全优先级（从高到低）：
    1. 黑名单 URL       → 立即拦截
    2. 黑名单方法       → 立即拦截
    3. 生产环境只读     → 拦截所有写方法
    4. 只读白名单       → 放行
    5. 非生产环境       → 全部放行

用法：
    from core.engine.env_guard import EnvGuard

    guard = EnvGuard()
    guard.check("POST", "/coupApply/cms/orderPay")   # 生产环境抛 SafetyBlockedError
"""
import logging
import time
from typing import Any, List, Optional, Set

from config import settings, get_current_env, is_production
from common.exceptions import SafetyBlockedError

logger = logging.getLogger(__name__)


class EnvGuard:
    """
    环境安全守卫（静态方法）
    """

    # 常见写操作方法
    WRITE_METHODS: Set[str] = {"POST", "PUT", "DELETE", "PATCH"}

    # ============================================================
    # 1. 核心检查入口
    # ============================================================
    @classmethod
    def check(
            cls,
            method: str,
            url: str,
            payload: Optional[Any] = None,
    ) -> None:
        """
        检查请求是否允许执行

        :param method:  HTTP 方法（GET / POST / PUT / DELETE / PATCH）
        :param url:     请求 URL（可以是完整 URL 或路径）
        :param payload: 请求体（用于审计日志）

        :raises SafetyBlockedError: 拦截
        """
        method = (method or "GET").upper()
        url_path = cls._normalize_url(url)

        # ---------- 1. 黑名单 URL 优先检查（无论什么环境，命中即拦） ----------
        forbidden_urls = set(
            settings.get("safety.forbidden_urls", []) or []
        )
        if url_path in forbidden_urls:
            cls._raise_blocked(
                method, url_path,
                reason=f"命中黑名单 URL（接口文档中的高危写接口）",
                payload=payload,
            )

        # ---------- 2. 黑名单方法（如显式配置了 PUT/DELETE） ----------
        forbidden_methods = set(
            settings.get("safety.forbidden_methods", []) or []
        )
        if method in forbidden_methods:
            cls._raise_blocked(
                method, url_path,
                reason=f"命中黑名单方法 {method}",
                payload=payload,
            )

        # ---------- 3. 非生产环境：全部放行 ----------
        if not is_production():
            # 但要为高危接口打审计日志（UAT 场景）
            cls._audit_if_needed(method, url_path, payload)
            return

        # ---------- 4. 生产环境：强制只读 ----------
        allow_write = settings.get("safety.allow_write_operations", False)
        if allow_write:
            # 配置错误场景：生产环境显式开启了写操作，打 WARNING 但仍拦截
            logger.warning(
                f"[EnvGuard] ⚠️ 生产环境配置了 allow_write_operations=true，"
                f"但 EnvGuard 强制拦截（请检查 {get_current_env()}.yaml）"
            )

        # 4.1 先查只读白名单（允许的只读类 POST）
        whitelist = cls._get_readonly_whitelist()
        if url_path in whitelist:
            logger.debug(
                f"[EnvGuard] 只读白名单放行: {method} {url_path}"
            )
            return

        # 4.2 只读方法（GET / HEAD / OPTIONS）放行
        if method in {"GET", "HEAD", "OPTIONS"}:
            cls._audit_if_needed(method, url_path, payload)
            return

        # 4.3 剩余写方法 → 拦截
        cls._raise_blocked(
            method, url_path,
            reason=f"生产环境禁止写操作（allow_write_operations=false）",
            payload=payload,
        )

    # ============================================================
    # 2. 批量检查（用于 YAML setup/teardown 阶段）
    # ============================================================
    @classmethod
    def check_many(cls, requests: List[dict]) -> None:
        """
        批量检查多个请求

        :param requests: [{"method": "POST", "url": "/xxx"}, ...]
        """
        for req in requests:
            cls.check(req["method"], req["url"], req.get("payload"))

    # ============================================================
    # 3. 便捷方法：直接判断是否允许
    # ============================================================
    @classmethod
    def is_allowed(cls, method: str, url: str) -> bool:
        """
        判断某请求是否被允许（不抛异常）

        用途：YAML 引擎预检阶段，跳过被拦截的用例而不是直接失败
        """
        try:
            cls.check(method, url)
            return True
        except SafetyBlockedError:
            return False

    # ============================================================
    # 4. 只读白名单
    # ============================================================
    @classmethod
    def _get_readonly_whitelist(cls) -> Set[str]:
        """获取只读接口白名单"""
        whitelist = set(
            settings.get("safety.readonly_whitelist.urls", []) or []
        )
        # 兼容部分写法的 POST 只读接口（文档中按条件查询用 POST）
        allowed_post = set(
            settings.get("safety.readonly_whitelist.allowed_readonly_post", []) or []
        )
        return whitelist | allowed_post

    # ============================================================
    # 5. 审计日志
    # ============================================================
    @classmethod
    def _audit_if_needed(cls, method: str, url: str, payload: Optional[Any]) -> None:
        """
        高危操作审计日志
        用于 UAT 等非生产环境下的操作追溯
        """
        audit_enabled = settings.get("safety.audit_log_for_high_risk", False)
        if not audit_enabled:
            return

        high_risk_urls = set(
            settings.get("safety.high_risk_urls", []) or []
        )
        if url in high_risk_urls:
            logger.warning(
                f"[AUDIT] {time.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"env={get_current_env()} | {method} {url} | "
                f"payload={_truncate(payload)}"
            )

    # ============================================================
    # 6. 内部辅助
    # ============================================================
    @staticmethod
    def _normalize_url(url: str) -> str:
        """
        归一化 URL 用于比对：
        - 完整 URL → 只取路径
        - 去掉末尾斜杠和查询参数
        """
        if not url:
            return ""

        if url.startswith("http://") or url.startswith("https://"):
            try:
                from urllib.parse import urlparse
                return urlparse(url).path or url
            except Exception:
                return url

        # 去掉 query 部分
        path = url.split("?")[0]
        # 去掉末尾的 /
        return path.rstrip("/") or "/"

    @classmethod
    def _raise_blocked(
            cls,
            method: str,
            url: str,
            reason: str,
            payload: Optional[Any] = None,
    ) -> None:
        """抛出拦截异常"""
        logger.error(
            f"[EnvGuard] 🛑 生产环境拦截: {method} {url} | "
            f"原因: {reason} | payload={_truncate(payload)}"
        )
        raise SafetyBlockedError(url=url, method=method, reason=reason)


# ============================================================
# 内部辅助
# ============================================================
def _truncate(payload: Any, max_len: int = 200) -> str:
    """安全截断 payload 用于日志"""
    if payload is None:
        return "None"
    s = str(payload)
    return s if len(s) <= max_len else s[:max_len] + "..."