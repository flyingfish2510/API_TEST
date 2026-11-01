# -*- coding: utf-8 -*-
"""
core/strategy/__init__.py
-------------------------
执行策略模块导出入口（API_TEST 框架）

聚合导出：
    - RetryPolicy：动态重试策略（区分网络异常与业务断言失败）
    - IncrementalRunner：增量执行策略（基于 Git Diff 匹配用例）

使用示例（引擎层或 CI 脚本调用）：
    from core.strategy import RetryPolicy, IncrementalRunner

    # 1. 动态重试
    def run_with_retry(fn, max_retries=2):
        return RetryPolicy.execute(fn, max_retries=max_retries)

    # 2. 增量执行
    plan = IncrementalRunner.calculate_plan("changed_files.txt")
    print(plan["files"])
"""

import logging

# ============================================================
# 1. 动态重试策略
# ============================================================
from .retry_policy import (
    RetryPolicy,
    is_retryable_exception,
)

# ============================================================
# 2. 增量执行策略
# ============================================================
from .incremental_runner import (
    IncrementalRunner,
    ModuleMapping,
)

__version__ = "1.0.0"

__all__ = [
    # 重试
    "RetryPolicy",
    "is_retryable_exception",
    # 增量
    "IncrementalRunner",
    "ModuleMapping",
]

# 初始化提示日志
logger = logging.getLogger(__name__)
logger.debug(f"[Strategy] 执行策略模块已加载 (version={__version__})")