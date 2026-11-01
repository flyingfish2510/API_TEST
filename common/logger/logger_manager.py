# -*- coding: utf-8 -*-
"""
common/logger/logger_manager.py
日志管理器（最小可用版）
"""
import os
import sys
import logging
from loguru import logger as _loguru_logger

# 确保日志目录存在
os.makedirs("reports/logs", exist_ok=True)

# 移除 loguru 默认 handler
_loguru_logger.remove()

# 控制台输出
_loguru_logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[trace_id]}</cyan> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    colorize=True,
    filter=lambda record: record["extra"].setdefault("trace_id", "-") or True,
)

# 文件输出
_loguru_logger.add(
    "reports/logs/api_test.log",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[trace_id]} | {name}:{function}:{line} - {message}",
    rotation="20 MB",
    retention="14 days",
    encoding="utf-8",
    enqueue=True,
    filter=lambda record: record["extra"].setdefault("trace_id", "-") or True,
)


class LoggerManager:
    """日志管理器"""

    @staticmethod
    def get_logger():
        return _loguru_logger

    @staticmethod
    def bind(**kwargs):
        return _loguru_logger.bind(**kwargs)

    @staticmethod
    def set_level(level: str):
        _loguru_logger.remove()
        _loguru_logger.add(sys.stdout, level=level)


# 关键：导出 logger
logger = _loguru_logger