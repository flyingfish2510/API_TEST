# -*- coding: utf-8 -*-
"""
core/__init__.py
----------------
核心引擎模块导出入口（API_TEST 框架）

聚合导出：
    - 核心执行引擎（YamlRunner, YamlLoader 等）
    - 断言引擎（AssertFactory 及 6 大断言器）
    - 数据契约（RequestConfig, CaseResult 等 Pydantic 模型）
    - 数据回收（TrashBin）
    - 外部打桩（MockServer, MockLoader）
    - 报告装配（AllureAttacher, QualityReporter）
    - 执行策略（RetryPolicy, IncrementalRunner）

使用示例：
    from core import YamlRunner, AssertFactory, TrashBin
"""

import logging

# ============================================================
# 1. 执行引擎层
# ============================================================
from .engine import (
    YamlLoader,
    YamlRunner,
    VariableResolver,
    RequestBuilder,
    Extractor,
    EnvGuard,
)

# ============================================================
# 2. 断言引擎层
# ============================================================
from .assertions import (
    AssertFactory,
    BaseAssert,
    SmartAssert,
    SchemaAssert,
    DBAssert,
    SnapshotAssert,
    PerfAssert,
)

# ============================================================
# 3. 数据契约层
# ============================================================
from .models import (
    HttpMethod,
    RequestConfig,
    ExtractRule,
    AssertRule,
    TestStep,
    ResponseData,
    AssertResult,
    CaseResult,
    CaseStatus,
)

# ============================================================
# 4. 数据回收层
# ============================================================
from .cleanup import (
    TrashBin,
    ResourceType,
    get_trash_bin,
)

# ============================================================
# 5. 外部打桩层
# ============================================================
from .mock import (
    MockServer,
    MockLoader,
    MockRule,
)

# ============================================================
# 6. 报告装配层
# ============================================================
from .reporter import (
    AllureAttacher,
    AllureEnvWriter,
    write_allure_env,
    HistoryManager,
    QualityReporter,
)

# ============================================================
# 7. 执行策略层
# ============================================================
from .strategy import (
    RetryPolicy,
    is_retryable_exception,
    IncrementalRunner,
    ModuleMapping,
)

# 版本信息
__version__ = "1.0.0"

# 初始化提示
logger = logging.getLogger(__name__)
logger.debug(f"[Core] 核心引擎层加载完成 (version={__version__})")

# 限定的导出列表
__all__ = [
    # --- Engine ---
    "YamlLoader",
    "YamlRunner",
    "VariableResolver",
    "RequestBuilder",
    "Extractor",
    "EnvGuard",

    # --- Assertions ---
    "AssertFactory",
    "BaseAssert",
    "SmartAssert",
    "SchemaAssert",
    "DBAssert",
    "SnapshotAssert",
    "PerfAssert",

    # --- Models ---
    "HttpMethod",
    "RequestConfig",
    "ExtractRule",
    "AssertRule",
    "TestStep",
    "ResponseData",
    "AssertResult",
    "CaseResult",
    "CaseStatus",

    # --- Cleanup ---
    "TrashBin",
    "ResourceType",
    "get_trash_bin",

    # --- Mock ---
    "MockServer",
    "MockLoader",
    "MockRule",

    # --- Reporter ---
    "AllureAttacher",
    "AllureEnvWriter",
    "write_allure_env",
    "HistoryManager",
    "QualityReporter",

    # --- Strategy ---
    "RetryPolicy",
    "is_retryable_exception",
    "IncrementalRunner",
    "ModuleMapping",
]