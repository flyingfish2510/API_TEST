# -*- coding: utf-8 -*-
"""
core/assertions/perf_assert.py
------------------------------
性能断言器（PerfAssert）

支持的断言：
    - assert_max_time:    单次请求耗时 <= 阈值
    - assert_min_time:    单次请求耗时 >= 阈值（防伪响应）
    - assert_p95_time:    P95 耗时（从 context 的历史记录统计）
    - assert_avg_time:    平均耗时
    - assert_percentile:  任意百分位耗时

依赖：
    - common/context.py（读取 last_elapsed_ms 与历史耗时）
    - config/settings.py（读取 performance_baselines 配置）

设计原则：
    - 毫秒级精度
    - 类型安全（浮点数比较，容忍极小误差）
    - 从 context 自动取耗时（无需每次传参）
    - 支持降级为警告模式
"""
import logging
import statistics
from typing import Any, List, Optional

from common.context import context
# ★ 关键：用 ApiAssertionError 别名，避免与内置 AssertionError 冲突
from common.exceptions import ApiAssertionError as AssertionError, PerformanceError
from config import settings

logger = logging.getLogger(__name__)


class PerfAssert:
    """
    性能断言器（静态方法）

    耗时数据来源：
        1. 单次断言：从 context.get("last_elapsed_ms") 读取
        2. 统计断言（P95/均值）：从 context.get("elapsed_history") 读取
    """

    # 统计的历史耗时最大保留数
    HISTORY_MAX_SIZE = 100

    # ============================================================
    # 1. 单次请求最大耗时断言
    # ============================================================
    @staticmethod
    def assert_max_time(response: Any, max_ms: float) -> None:
        """
        断言单次请求耗时 <= 阈值

        :param response: HttpResponse 对象
        :param max_ms:   最大允许耗时（毫秒）

        示例：
            - max_time: 500    # 响应必须在 500ms 内
        """
        elapsed = PerfAssert._get_elapsed(response)

        if elapsed > max_ms:
            raise PerformanceError(
                path=_response_path(response),
                elapsed_ms=elapsed,
                threshold_ms=max_ms,
            )

        logger.info(
            f"[PerfAssert] ✅ max_time 通过: {elapsed:.2f}ms <= {max_ms}ms"
        )

    # ============================================================
    # 2. 单次请求最小耗时断言
    # ============================================================
    @staticmethod
    def assert_min_time(response: Any, min_ms: float) -> None:
        """
        断言单次请求耗时 >= 阈值

        用途：防止"伪响应"（如缓存直接返回 None 导致的极快响应）

        示例：
            - min_time: 10    # 正常业务响应不应该低于 10ms
        """
        elapsed = PerfAssert._get_elapsed(response)

        if elapsed < min_ms:
            raise AssertionError(
                f"{_response_path(response)} 响应耗时 {elapsed:.2f}ms "
                f"低于期望下限 {min_ms}ms（可能是伪响应）",
                detail={
                    "elapsed_ms": elapsed,
                    "threshold_ms": min_ms,
                    "reason": "too_fast",
                },
            )

        logger.info(
            f"[PerfAssert] ✅ min_time 通过: {elapsed:.2f}ms >= {min_ms}ms"
        )

    # ============================================================
    # 3. P95 耗时断言（从历史记录统计）
    # ============================================================
    @staticmethod
    def assert_p95_time(response: Any, max_p95_ms: float) -> None:
        """
        断言历史耗时的 P95 值 <= 阈值

        说明：从 context.get("elapsed_history") 读取该接口的历史耗时列表。
        每次 HTTP 请求后，HTTP Client 会自动 append 到历史。

        示例：
            - p95_time: 1000   # 95% 的请求耗时在 1000ms 内
        """
        history = PerfAssert._get_history()

        if len(history) < 3:
            logger.warning(
                f"[PerfAssert] 历史样本不足（{len(history)} 条），跳过 p95 断言"
            )
            return

        p95 = _percentile(history, 95)

        if p95 > max_p95_ms:
            raise PerformanceError(
                path=f"P95({_response_path(response)})",
                elapsed_ms=p95,
                threshold_ms=max_p95_ms,
            )

        logger.info(
            f"[PerfAssert] ✅ p95_time 通过: P95={p95:.2f}ms <= {max_p95_ms}ms "
            f"(样本 {len(history)} 条)"
        )

    # ============================================================
    # 4. 平均耗时断言
    # ============================================================
    @staticmethod
    def assert_avg_time(response: Any, max_avg_ms: float) -> None:
        """
        断言历史耗时的平均值 <= 阈值
        """
        history = PerfAssert._get_history()

        if not history:
            logger.warning("[PerfAssert] 无历史样本，跳过 avg 断言")
            return

        avg = statistics.mean(history)

        if avg > max_avg_ms:
            raise PerformanceError(
                path=f"AVG({_response_path(response)})",
                elapsed_ms=avg,
                threshold_ms=max_avg_ms,
            )

        logger.info(
            f"[PerfAssert] ✅ avg_time 通过: AVG={avg:.2f}ms <= {max_avg_ms}ms"
        )

    # ============================================================
    # 5. 任意百分位断言
    # ============================================================
    @staticmethod
    def assert_percentile(
        response: Any,
        percentile: float,
        max_ms: float,
    ) -> None:
        """
        断言任意百分位的耗时 <= 阈值

        示例：
            - percentile:
                p: 99
                max: 3000
        """
        history = PerfAssert._get_history()

        if len(history) < 3:
            logger.warning("[PerfAssert] 历史样本不足，跳过 percentile 断言")
            return

        value = _percentile(history, percentile)

        if value > max_ms:
            raise PerformanceError(
                path=f"P{percentile}({_response_path(response)})",
                elapsed_ms=value,
                threshold_ms=max_ms,
            )

        logger.info(
            f"[PerfAssert] ✅ P{percentile} 通过: {value:.2f}ms <= {max_ms}ms"
        )

    # ============================================================
    # 6. 记录耗时到历史（供 HTTP Client 调用）
    # ============================================================
    @staticmethod
    def record_elapsed(elapsed_ms: float) -> None:
        """
        将本次耗时记录到 context 的历史列表
        在 HttpClient 每次请求完成后自动调用
        """
        history = context.get("elapsed_history", scope="case") or []
        history.append(float(elapsed_ms))

        # 限制历史最大长度
        if len(history) > PerfAssert.HISTORY_MAX_SIZE:
            history = history[-PerfAssert.HISTORY_MAX_SIZE:]

        context.set("elapsed_history", history, scope="case")

    # ============================================================
    # 内部辅助
    # ============================================================
    @staticmethod
    def _get_elapsed(response: Any) -> float:
        """
        获取本次请求耗时（毫秒）

        优先级：
            1. context.get("last_elapsed_ms")
            2. response.elapsed.total_seconds() * 1000
        """
        elapsed = context.get("last_elapsed_ms", scope="case")
        if elapsed is not None:
            return float(elapsed)

        # 兜底：从 response 对象推算
        if response is not None and hasattr(response, "elapsed"):
            return response.elapsed.total_seconds() * 1000

        raise AssertionError(
            "无法获取响应耗时，context 与 response 均无耗时信息",
            detail={"reason": "no_elapsed_info"},
        )

    @staticmethod
    def _get_history() -> List[float]:
        """获取当前用例的历史耗时列表"""
        return context.get("elapsed_history", scope="case") or []


# ============================================================
# 响应路径辅助（用于错误信息更友好）
# ============================================================
def _response_path(response: Any) -> str:
    """从 response 对象提取 URL 路径（用于错误消息）"""
    if response is None:
        return "unknown_url"
    url = getattr(response, "url", "") or ""
    # 尝试只取路径部分
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.path or url
    except Exception:
        return url[:80]


# ============================================================
# 百分位计算（无第三方依赖）
# ============================================================
def _percentile(data: List[float], pct: float) -> float:
    """
    计算百分位值（线性插值法，与 numpy.percentile 默认行为一致）

    :param data: 数据列表
    :param pct:  百分位（0-100）
    """
    if not data:
        return 0.0
    if len(data) == 1:
        return float(data[0])

    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f

    if f == c:
        return float(sorted_data[int(k)])

    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return float(d0 + d1)