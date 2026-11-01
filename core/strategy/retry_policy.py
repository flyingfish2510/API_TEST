# -*- coding: utf-8 -*-
"""
core/strategy/retry_policy.py
-----------------------------
动态重试策略（RetryPolicy）

职责：
    1. 提供重试执行器（同步）
    2. 区分可重试异常（网络/5xx）与不可重试异常（业务失败/断言失败）
    3. 提供指数退避机制，避免“重试风暴”打垮下游服务
    4. 提供装饰器用法

依赖：
    无第三方依赖
"""
import time
import logging
import functools
from typing import Any, Callable, Type, Tuple, Union

from common.exceptions import (
    API_TESTException,
    NetworkError,
    TimeoutError,
    ApiAssertionError,
    RetryExhaustedError,
    SafetyBlockedError,
)

logger = logging.getLogger(__name__)


# ============================================================
# 1. 异常分类器
# ============================================================
def is_retryable_exception(exc: BaseException) -> bool:
    """
    判断异常是否允许重试

    :param exc: 捕获到的异常
    :return:    True 表示允许重试
    """
    # 1. 业务断言失败 → 绝对不重试（重试也没有意义）
    if isinstance(exc, ApiAssertionError):
        return False

    # 2. 安全拦截 → 绝对不重试（重试只会继续被拦截）
    if isinstance(exc, SafetyBlockedError):
        return False

    # 3. 框架环境级异常（网络/超时/服务不可用）→ 允许重试
    if isinstance(exc, (NetworkError, TimeoutError)):
        return True

    # 4. 未预期异常 → 不允许重试（可能是代码 Bug）
    return False


def _check_http_response_retryable(response: Any) -> bool:
    """
    判断 HTTP 响应是否可重试（针对 5xx 错误）

    :param response: requests.Response 对象
    """
    if response is None:
        return False

    # 502 Bad Gateway / 503 Service Unavailable / 504 Gateway Timeout
    if response.status_code in (502, 503, 504):
        return True

    return False


# ============================================================
# 2. 重试策略执行器
# ============================================================
class RetryPolicy:
    """
    动态重试策略（静态方法）
    """

    # ============================================================
    # 1. 执行器（主入口）
    # ============================================================
    @classmethod
    def execute(
            cls,
            func: Callable,
            *args,
            max_retries: int = 2,
            delay: float = 0.5,
            backoff: float = 2.0,
            retryable_exceptions: Tuple[Type[Exception], ...] = None,
            **kwargs
    ) -> Any:
        """
        带重试地执行函数

        :param func:                 待执行的函数
        :param max_retries:          最大重试次数（不含首次执行）
        :param delay:                初始重试间隔（秒）
        :param backoff:              退避倍数（如 0.5 → 1.0 → 2.0）
        :param retryable_exceptions: 允许重试的异常元组（默认使用 is_retryable_exception 判断）
        :param args, kwargs:         传给 func 的参数
        :return:                     func 的返回值
        :raises RetryExhaustedError: 重试耗尽仍失败
        """
        attempt = 0
        current_delay = delay

        while True:
            try:
                result = func(*args, **kwargs)

                # 特殊处理：如果返回的是 5xx 响应，也触发重试
                if hasattr(result, "status_code") and _check_http_response_retryable(result):
                    if attempt < max_retries:
                        logger.warning(
                            f"[Retry] HTTP {result.status_code} 响应，"
                            f"第 {attempt + 1}/{max_retries} 次重试（{current_delay}s 后）"
                        )
                        time.sleep(current_delay)
                        attempt += 1
                        current_delay *= backoff
                        continue

                # 成功，返回结果
                if attempt > 0:
                    logger.info(f"[Retry] ✅ 第 {attempt} 次重试成功")
                return result

            except Exception as e:
                # 判断是否允许重试
                should_retry = False
                if retryable_exceptions:
                    should_retry = isinstance(e, retryable_exceptions)
                else:
                    should_retry = is_retryable_exception(e)

                if not should_retry:
                    # 不允许重试，立刻抛出
                    logger.debug(f"[Retry] ❌ 不可重试异常，直接抛出: {type(e).__name__}")
                    raise

                # 允许重试，检查次数
                if attempt >= max_retries:
                    logger.error(
                        f"[Retry] 💥 重试 {max_retries} 次后仍然失败: {e}"
                    )
                    raise RetryExhaustedError(
                        url=getattr(e, "url", "unknown"),
                        attempts=attempt,
                    ) from e

                # 执行重试
                attempt += 1
                logger.warning(
                    f"[Retry] ⚠️ 第 {attempt}/{max_retries} 次重试 "
                    f"({type(e).__name__}: {str(e)[:80]})，{current_delay}s 后... "
                )
                time.sleep(current_delay)
                current_delay *= backoff

    # ============================================================
    # 2. 装饰器模式（可选）
    # ============================================================
    @classmethod
    def decorator(
            cls,
            max_retries: int = 2,
            delay: float = 0.5,
            backoff: float = 2.0,
            retryable_exceptions: Tuple[Type[Exception], ...] = None,
    ):
        """
        装饰器用法：
            @RetryPolicy.decorator(max_retries=3)
            def flaky_api(): ...
        """

        def wrapper(func):
            @functools.wraps(func)
            def inner(*args, **kwargs):
                return cls.execute(
                    func,
                    *args,
                    max_retries=max_retries,
                    delay=delay,
                    backoff=backoff,
                    retryable_exceptions=retryable_exceptions,
                    **kwargs
                )

            return inner

        return wrapper