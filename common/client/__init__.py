# -*- coding: utf-8 -*-
"""
API_TEST 公共客户端模块
----------------------
对外暴露 HTTP 请求发送、鉴权管理以及 Trace-ID 链路追踪的核心接口。
"""
import logging

# 1. 导出核心类，方便外部通过 `from common.client import HttpClient` 直接使用
from .http_client import HttpClient
from .auth import AuthManager
from .trace_context import TraceContext

# 2. 定义模块版本号（可选，便于排查框架自身版本问题）
__version__ = "1.0.0"

# 3. 定义当使用者执行 `from common.client import *` 时暴露的内容
__all__ = [
    "HttpClient",
    "AuthManager",
    "TraceContext"
]

# 4. 模块级别的初始化日志（可选）
logger = logging.getLogger(__name__)
logger.debug(f"Initialized common.client module (version={__version__})")