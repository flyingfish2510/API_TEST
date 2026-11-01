# -*- coding: utf-8 -*-
"""
core/reporter/allure_attacher.py
--------------------------------
Allure 报告附件管理器（AllureAttacher）

职责：
    1. 附加请求参数（自动脱敏）
    2. 附加响应数据（状态码/耗时/Header/Body）
    3. 附加异常信息（含堆栈）
    4. 附加提取的变量
    5. 附加日志片段
"""
import json
import logging
from typing import Any, Dict, Optional

import allure

logger = logging.getLogger(__name__)

# 需要脱敏的字段（不区分大小写）
_SENSITIVE_FIELDS = {
    "password", "passwd", "pwd", "token", "secret",
    "authorization", "cookie", "session", "sessionid",
    "access_token", "refresh_token", "private_key",
    "creditidentifier", "idcard", "id_card",
}


class AllureAttacher:
    """
    Allure 附件管理器（静态方法）
    """

    # ============================================================
    # 1. 附加请求
    # ============================================================
    @staticmethod
    def attach_request(request_data: Dict[str, Any]) -> None:
        """
        附加请求参数到 Allure

        :param request_data: 请求配置字典（method / url / headers / json / data / params）
        """
        if not request_data:
            return

        # 脱敏
        sanitized = _sanitize(request_data)

        method = sanitized.get("method", "GET")
        url = sanitized.get("url", "")

        # 构造可读内容
        content = _build_request_content(sanitized)

        allure.attach(
            content,
            name=f"Request: {method} {_short_url(url)}",
            attachment_type=allure.attachment_type.TEXT,
        )

        logger.debug(f"[Allure] 附加请求: {method} {url}")

    # ============================================================
    # 2. 附加响应
    # ============================================================
    @staticmethod
    def attach_response(response_data: Any) -> None:
        """
        附加响应到 Allure

        :param response_data: ResponseData 对象 或 requests.Response 对象
        """
        if response_data is None:
            return

        # 兼容不同来源
        status_code = getattr(response_data, "status_code", 0)
        headers = getattr(response_data, "headers", {}) or {}
        text = getattr(response_data, "text", "") or ""
        elapsed_ms = getattr(response_data, "elapsed_ms", None)

        # ResponseData 有 elapsed_ms，requests.Response 要计算
        if elapsed_ms is None:
            elapsed = getattr(response_data, "elapsed", None)
            if elapsed is not None:
                elapsed_ms = round(elapsed.total_seconds() * 1000, 2)
            else:
                elapsed_ms = 0.0

        # 脱敏 header
        sanitized_headers = _sanitize(dict(headers))

        # 截断过大的响应
        text_truncated = _truncate(text, max_len=5000)

        # 构造内容
        header_str = "\n".join(f"  {k}: {v}" for k, v in sanitized_headers.items())
        content = (
            f"Status Code: {status_code}\n"
            f"Elapsed: {elapsed_ms:.2f}ms\n"
            f"\n--- Headers ---\n{header_str}\n"
            f"\n--- Body ---\n{text_truncated}"
        )

        allure.attach(
            content,
            name=f"Response: {status_code} ({elapsed_ms:.0f}ms)",
            attachment_type=allure.attachment_type.TEXT,
        )

        logger.debug(f"[Allure] 附加响应: {status_code}")

    # ============================================================
    # 3. 附加异常
    # ============================================================
    @staticmethod
    def attach_error(action: str, exc: Exception) -> None:
        """
        附加异常信息到 Allure

        :param action: 操作描述（如 "变量解析" / "请求发送" / "断言校验"）
        :param exc:    异常对象
        """
        if exc is None:
            return

        error_type = type(exc).__name__
        error_msg = str(exc)

        # 提取 API_TESTException 的结构化详情
        detail = ""
        if hasattr(exc, "to_dict") and callable(getattr(exc, "to_dict")):
            try:
                detail_dict = exc.to_dict()
                detail = f"\n\n--- Detail ---\n{json.dumps(detail_dict, ensure_ascii=False, indent=2)}"
            except Exception:
                pass

        # 提取异常链（如果有 from）
        cause = ""
        if exc.__cause__ is not None:
            cause = f"\n\n--- Caused By ---\n{type(exc.__cause__).__name__}: {exc.__cause__}"

        # 提取堆栈
        import traceback
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb_str = "".join(tb_lines)

        content = (
            f"Action: {action}\n"
            f"Error: {error_type}: {error_msg}"
            f"{detail}"
            f"{cause}"
            f"\n\n--- Traceback ---\n{tb_str}"
        )

        allure.attach(
            content,
            name=f"Error: {action}",
            attachment_type=allure.attachment_type.TEXT,
        )

        logger.debug(f"[Allure] 附加异常: {action} - {error_type}")

    # ============================================================
    # 4. 附加提取变量
    # ============================================================
    @staticmethod
    def attach_extract(extracted: Dict[str, Any]) -> None:
        """
        附加提取的变量到 Allure

        :param extracted: {变量名: 变量值} 字典
        """
        if not extracted:
            return

        sanitized = _sanitize(extracted)
        content = json.dumps(sanitized, ensure_ascii=False, indent=2)

        allure.attach(
            content,
            name="Extracted Variables",
            attachment_type=allure.attachment_type.JSON,
        )

        logger.debug(f"[Allure] 附加提取变量: {list(extracted.keys())}")

    # ============================================================
    # 5. 附加日志
    # ============================================================
    @staticmethod
    def attach_log(log_content: str, name: str = "Logs") -> None:
        """
        附加日志片段到 Allure

        :param log_content: 日志文本
        :param name:        附件名称
        """
        if not log_content:
            return

        allure.attach(
            _truncate(log_content, max_len=10000),
            name=name,
            attachment_type=allure.attachment_type.TEXT,
        )


# ============================================================
# 内部辅助函数
# ============================================================
def _sanitize(data: Any, max_depth: int = 5) -> Any:
    """
    递归脱敏（password / token / cookie 等敏感字段替换为 ***MASKED***）
    """
    if max_depth <= 0:
        return data

    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(field in key_lower for field in _SENSITIVE_FIELDS):
                result[k] = "***MASKED***"
            else:
                result[k] = _sanitize(v, max_depth - 1)
        return result

    if isinstance(data, list):
        return [_sanitize(item, max_depth - 1) for item in data]

    return data


def _build_request_content(req: Dict[str, Any]) -> str:
    """构造请求内容的可读文本"""
    lines = [f"Method: {req.get('method', 'GET')}"]
    lines.append(f"URL: {req.get('url', '')}")

    headers = req.get("headers") or {}
    if headers:
        lines.append("")
        lines.append("--- Headers ---")
        for k, v in headers.items():
            lines.append(f"  {k}: {v}")

    params = req.get("params")
    if params:
        lines.append("")
        lines.append("--- Params ---")
        lines.append(json.dumps(params, ensure_ascii=False, indent=2))

    json_body = req.get("json")
    if json_body is not None:
        lines.append("")
        lines.append("--- JSON Body ---")
        lines.append(json.dumps(json_body, ensure_ascii=False, indent=2))

    data = req.get("data")
    if data is not None:
        lines.append("")
        lines.append("--- Form Data ---")
        if isinstance(data, dict):
            lines.append(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            lines.append(str(data))

    files = req.get("files")
    if files:
        lines.append("")
        lines.append("--- Files ---")
        for k, v in files.items():
            lines.append(f"  {k}: {v}")

    return "\n".join(lines)


def _truncate(text: str, max_len: int = 5000) -> str:
    """截断超长文本"""
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n\n... (truncated, total {len(text)} chars)"


def _short_url(url: str, max_len: int = 60) -> str:
    """缩短 URL 用于附件名（避免文件名过长）"""
    if not url:
        return ""
    if len(url) <= max_len:
        return url
    return "..." + url[-(max_len - 3):]