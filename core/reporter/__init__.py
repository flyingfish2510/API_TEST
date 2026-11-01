# -*- coding: utf-8 -*-
"""
core/reporter/__init__.py
-------------------------
报告模块导出入口（API_TEST 框架）

聚合导出：
    - AllureAttacher：Allure 报告附件管理器（请求/响应/日志/错误）
    - AllureEnvWriter：Allure 环境信息写入器（environment.properties）
    - HistoryManager：历史趋势数据管理器（复制 history 目录）
    - QualityReporter：质量报告生成器（quality-report.json）

使用示例（由 pytest_plugins / YamlRunner 调用）：
    from core.reporter import AllureAttacher, write_allure_env

    # 附加请求响应到 Allure
    AllureAttacher.attach_request_response(request_data, response_data)

    # 写入环境信息
    write_allure_env("reports/allure-results")
"""

import logging

# ============================================================
# 1. Allure 报告附件
# ============================================================
from .allure_attacher import AllureAttacher

# ============================================================
# 2. Allure 环境信息写入
# ============================================================
from .allure_env_writer import (
    AllureEnvWriter,
    write_allure_env,
)

# ============================================================
# 3. 历史趋势管理
# ============================================================
from .history_manager import HistoryManager

# ============================================================
# 4. 质量报告生成
# ============================================================
from .quality_reporter import QualityReporter

__version__ = "1.0.0"

__all__ = [
    # Allure 附件
    "AllureAttacher",
    # Allure 环境信息
    "AllureEnvWriter",
    "write_allure_env",
    # 历史趋势
    "HistoryManager",
    # 质量报告
    "QualityReporter",
]

# 初始化提示日志
logger = logging.getLogger(__name__)
logger.debug(f"[Reporter] 报告模块已加载 (version={__version__})")