# -*- coding: utf-8 -*-
"""
core/engine/request_builder.py
------------------------------
HTTP 请求组装器（RequestBuilder）

职责：
    1. 将 YAML 中解析好的 request 字典转换为 HTTP 请求参数
    2. 智能处理 JSON、Form 表单、文件上传、URL 参数
    3. 自动合并 Header（含鉴权）
    4. 统一调用 HttpClient 发送请求

支持的 YAML request 结构：
    request:
      method: "POST"
      url: "/dar/user/login"
      headers:
        Content-Type: "application/x-www-form-urlencoded"
      data:                         # 对应 form 表单
        user_name: "test01"
        passwd: "admin123"
      # 或
      json:                         # 对应 JSON 请求体
        user_id: 123
      params:                       # 对应 URL 查询参数
        page: 1
      files:                        # 对应文件上传
        file: "@path/to/file.png"
"""
import logging
from typing import Any, Dict, Optional

from common.client import HttpClient
from common.exceptions import ConfigError
from config import settings

logger = logging.getLogger(__name__)


class RequestBuilder:
    """
    HTTP 请求组装器（静态方法）
    """

    # 默认 Content-Type
    DEFAULT_JSON_HEADERS = {
        "Content-Type": "application/json; charset=UTF-8"
    }
    DEFAULT_FORM_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    # ============================================================
    # 主入口
    # ============================================================
    @classmethod
    def build_and_send(
            cls,
            api_client: HttpClient,
            request_data: Dict[str, Any],
            global_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        组装并发送 HTTP 请求

        :param api_client:     HttpClient 实例（来自 fixture）
        :param request_data:   YAML 中的 request 节点（已解析变量）
        :param global_headers: 全局 Header（如 Auth Token）
        :return:               HttpResponse 对象
        """
        if not isinstance(request_data, dict):
            raise ConfigError(f"request 节点格式错误，应为 dict: {request_data}")

        # ---------- 1. 提取基础参数 ----------
        method = cls._get_method(request_data)
        url = cls._get_url(request_data)

        # ---------- 2. 组装 Headers ----------
        headers = cls._build_headers(request_data, global_headers)

        # ---------- 3. 组装请求体 ----------
        kwargs = {"headers": headers}
        cls._attach_body(request_data, headers, kwargs)

        # ---------- 4. 附加 URL 查询参数 ----------
        if "params" in request_data and request_data["params"] is not None:
            kwargs["params"] = request_data["params"]

        # ---------- 5. 其他高级选项 ----------
        if "timeout" in request_data:
            kwargs["timeout"] = int(request_data["timeout"])
        if "allow_redirects" in request_data:
            kwargs["allow_redirects"] = bool(request_data["allow_redirects"])

        # ---------- 6. 发送请求 ----------
        logger.debug(
            f"[RequestBuilder] 发送请求: {method} {url} "
            f"| content-type={headers.get('Content-Type', 'N/A')}"
        )

        return api_client.request(method, url, **kwargs)

    # ============================================================
    # 基础参数提取
    # ============================================================
    @staticmethod
    def _get_method(request_data: Dict[str, Any]) -> str:
        method = request_data.get("method", "GET").upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            raise ConfigError(f"不支持的 HTTP 方法: {method}")
        return method

    @staticmethod
    def _get_url(request_data: Dict[str, Any]) -> str:
        url = request_data.get("url")
        if not url:
            raise ConfigError("request 缺少 url 字段")
        return url

    # ============================================================
    # Headers 组装
    # ============================================================
    @classmethod
    def _build_headers(
            cls,
            request_data: Dict[str, Any],
            global_headers: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        """
        合并 Headers：默认 + 全局鉴权 + 局部定义
        """
        headers = {}

        # 1. 全局鉴权 Header（如 token、Cookie）
        if global_headers:
            headers.update(global_headers)

        # 2. YAML 中显式声明的 headers（优先级最高，可覆盖）
        yaml_headers = request_data.get("headers") or {}
        if yaml_headers:
            headers.update(yaml_headers)

        return headers

    # ============================================================
    # 请求体组装
    # ============================================================
    @classmethod
    def _attach_body(
            cls,
            request_data: Dict[str, Any],
            headers: Dict[str, str],
            kwargs: Dict[str, Any],
    ) -> None:
        """
        根据 YAML 声明的格式，组装请求体。
        优先级：files > json > data
        """
        # 1. 文件上传 (Multipart)
        if "files" in request_data and request_data["files"]:
            kwargs["files"] = request_data["files"]
            # 文件上传时，不要手动设置 Content-Type，交由 requests 自动处理 boundary
            headers.pop("Content-Type", None)
            # 如果有伴随的表单数据
            if "data" in request_data and request_data["data"]:
                kwargs["data"] = request_data["data"]
            return

        # 2. JSON 请求体
        if "json" in request_data and request_data["json"] is not None:
            kwargs["json"] = request_data["json"]
            # 自动补 Content-Type（如果未指定）
            if "content-type" not in {k.lower() for k in headers}:
                headers.update(cls.DEFAULT_JSON_HEADERS)
            return

        # 3. Form 表单 (x-www-form-urlencoded)
        if "data" in request_data and request_data["data"] is not None:
            kwargs["data"] = request_data["data"]
            # 如果未指定 Content-Type，默认用 Form
            if "content-type" not in {k.lower() for k in headers}:
                headers.update(cls.DEFAULT_FORM_HEADERS)
            return

        # 4. 无请求体（如 GET / DELETE）
        return