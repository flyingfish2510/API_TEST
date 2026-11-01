# -*- coding: utf-8 -*-
"""
core/plugins/allure_plugins.py
Allure 报告附件（最小占位版）
"""

import json
import logging
import allure

logger = logging.getLogger(__name__)


def attach_request_response(request_data=None, response_data=None):
    """附加请求/响应到 Allure（占位）"""
    try:
        if request_data is not None:
            allure.attach(
                str(request_data),
                name="Request",
                attachment_type=allure.attachment_type.TEXT,
            )
        if response_data is not None:
            allure.attach(
                str(response_data),
                name="Response",
                attachment_type=allure.attachment_type.TEXT,
            )
    except Exception as e:
        logger.warning(f"[Allure] attach_request_response 失败: {e}")


def attach_error(action: str = "", exc=None):
    """附加异常到 Allure（占位）"""
    try:
        import traceback
        error_type = type(exc).__name__ if exc else "Unknown"
        error_msg = str(exc) if exc else ""
        tb = ""
        if exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        content = f"Action: {action}\nError: {error_type}: {error_msg}\n\n{tb}"
        allure.attach(
            content,
            name=f"Error: {action or error_type}",
            attachment_type=allure.attachment_type.TEXT,
        )
    except Exception as e:
        logger.warning(f"[Allure] attach_error 失败: {e}")


def attach_environment(env_data=None):
    """附加环境信息到 Allure（占位）"""
    try:
        if env_data is None:
            return
        allure.attach(
            json.dumps(env_data, ensure_ascii=False, indent=2, default=str),
            name="Environment",
            attachment_type=allure.attachment_type.JSON,
        )
    except Exception as e:
        logger.warning(f"[Allure] attach_environment 失败: {e}")