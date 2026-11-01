# -*- coding: utf-8 -*-
"""
common/client/trace_context.py
------------------------------
全链路追踪上下文（TraceContext）

职责：
    1. 生成全局唯一 Trace-ID（兼容 W3C traceparent 标准）
    2. 线程安全地存取 Trace-ID（兼容 pytest-xdist）
    3. 自动附加到 HTTP 请求头
    4. 供日志系统提取，便于在 Kibana / SkyWalking 检索
"""
import uuid
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TraceContext:
    """
    线程安全的 Trace 上下文管理器

    用法：
        # 场景 1：自动管理（推荐在 Pytest Fixture 中使用）
        with TraceContext() as trace_id:
            # 内部所有日志和 HTTP 请求都会自动使用该 trace_id
            pass

        # 场景 2：手动获取
        trace_id = TraceContext.get_trace_id()
    """

    _thread_local = threading.local()

    def __init__(self, trace_id: Optional[str] = None):
        self._trace_id = trace_id or self.generate_trace_id()
        self._old_trace_id = None

    # ============================================================
    # 生成与获取
    # ============================================================
    @staticmethod
    def generate_trace_id() -> str:
        """生成 32 位十六进制 Trace-ID（符合 W3C Trace Context 标准）"""
        return uuid.uuid4().hex

    @staticmethod
    def get_trace_id() -> str:
        """获取当前线程的 Trace-ID；若不存在则生成一个新的"""
        trace_id = getattr(TraceContext._thread_local, "trace_id", None)
        if not trace_id:
            trace_id = TraceContext.generate_trace_id()
            TraceContext._thread_local.trace_id = trace_id
        return trace_id

    @staticmethod
    def set_trace_id(trace_id: str) -> None:
        """手动设置当前线程的 Trace-ID"""
        TraceContext._thread_local.trace_id = trace_id

    # ============================================================
    # 上下文管理器
    # ============================================================
    def __enter__(self) -> str:
        """进入上下文时，保存旧值并设置新值"""
        self._old_trace_id = getattr(TraceContext._thread_local, "trace_id", None)
        TraceContext._thread_local.trace_id = self._trace_id
        logger.debug(f"[Trace] 进入上下文: {self._trace_id}")
        return self._trace_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时，恢复旧值"""
        if self._old_trace_id:
            TraceContext._thread_local.trace_id = self._old_trace_id
        else:
            # 如果进入前没有 Trace-ID，则清空
            if hasattr(TraceContext._thread_local, "trace_id"):
                del TraceContext._thread_local.trace_id
        logger.debug(f"[Trace] 退出上下文，恢复为: {self._old_trace_id}")

    # ============================================================
    # W3C traceparent 格式生成（供微服务网关使用）
    # ============================================================
    @staticmethod
    def get_traceparent(trace_id: Optional[str] = None) -> str:
        """
        生成符合 W3C Trace Context 规范的 traceparent 头
        格式：{version}-{trace-id}-{parent-id}-{trace-flags}
        示例：00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
        """
        trace = trace_id or TraceContext.get_trace_id()
        span_id = uuid.uuid4().hex[:16]  # 当前请求的 span ID
        return f"00-{trace}-{span_id}-01"

    # ============================================================
    # 供日志系统使用的上下文信息
    # ============================================================
    @staticmethod
    def get_log_context() -> dict:
        """
        返回日志插件（如 Loguru）需要的上下文信息
        可以在日志格式化时自动注入
        """
        return {
            "trace_id": TraceContext.get_trace_id()[:8],  # 日志中只显示前 8 位，节省空间
        }