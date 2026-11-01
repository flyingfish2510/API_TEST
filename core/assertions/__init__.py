# -*- coding: utf-8 -*-
"""
core/assertions/__init__.py
---------------------------
断言引擎导出入口（API_TEST 框架）

聚合导出：
    - AssertFactory：断言工厂（引擎层唯一对外入口）
    - BaseAssert：基础断言（eq / contains / status_code 等）
    - SmartAssert：智能断言（regex / type / not_null）
    - SchemaAssert：JSON Schema 契约校验
    - DBAssert：数据库直连断言
    - SnapshotAssert：基线差分对比
    - PerfAssert：性能响应时间断言

使用示例（引擎层）：
    from core.assertions import AssertFactory

    AssertFactory.validate(response, [
        {"eq": ["status_code", 200]},
        {"eq": ["$.error_code", "0000"]},
        {"max_time": 500},
    ])
"""

# ============================================================
# 1. 工厂入口（供 YAML 引擎调用）
# ============================================================
from .assert_factory import AssertFactory

# ============================================================
# 2. 各断言器实现类（便于自定义扩展或单测）
# ============================================================
from .base_assert import BaseAssert
from .smart_assert import SmartAssert
from .schema_assert import SchemaAssert
from .db_assert import DBAssert
from .snapshot_assert import SnapshotAssert
from .perf_assert import PerfAssert

# ============================================================
# 版本与元信息
# ============================================================
__version__ = "1.0.0"

__all__ = [
    # 工厂（对外唯一推荐入口）
    "AssertFactory",
    # 断言器实现类
    "BaseAssert",
    "SmartAssert",
    "SchemaAssert",
    "DBAssert",
    "SnapshotAssert",
    "PerfAssert",
]