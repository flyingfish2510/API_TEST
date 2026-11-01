# -*- coding: utf-8 -*-
"""
common/client/http_client.py
----------------------------
HTTP 客户端封装（基于 requests）

特性：
    1. 支持 JSON / Form / Multipart 混合传参
    2. Session 复用，自动维护 Cookie
    3. Trace-ID 全链路追踪
    4. 智能重试（仅网络/5xx，不重试业务断言失败）
    5. 超时控制（connect/read 分离）
    6. 结构化日志（带耗时、带业务码）
    7. 响应自动缓存到 Context（供 YAML 提取与断言）
"""
import time
import uuid
import logging
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.context import context
from common.exceptions import NetworkError, TimeoutError

logger = logging.getLogger(__name__)


class HttpClient:
    """
    企业级 HTTP 客户端

    :param base_url: 基础 URL，如 http://127.0.0.1:8787
    :param timeout: 默认超时时间（秒）
    :param verify_ssl: 是否校验 SSL 证书（内网测试环境通常设 False）
    :param retry_times: 网络层重试次数
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: int = 15,
        verify_ssl: bool = False,
        retry_times: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.retry_times = retry_times

        # 初始化 Session
        self.session = requests.Session()
        self.session.verify = verify_ssl

        # 默认 Header
        self.session.headers.update({
            "User-Agent": "API_TEST/1.0 (Automation Framework)",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })

        # 配置网络重试策略（只对幂等方法生效：GET/HEAD/PUT/DELETE/OPTIONS）
        retry_strategy = Retry(
            total=retry_times,
            connect=retry_times,
            read=retry_times,
            status=retry_times,
            backoff_factor=0.5,                # 重试间隔：0.5s, 1s, 2s...
            status_forcelist=[502, 503, 504],  # 只对网关错误/超时重试
            allowed_methods=frozenset(["GET", "HEAD", "PUT", "DELETE", "OPTIONS"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=50,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ============================================================
    # 底层请求方法
    # ============================================================
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict] = None,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        files: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
        **kwargs,
    ) -> requests.Response:
        """
        发送 HTTP 请求（统一入口）

        :param method: HTTP 方法
        :param url: 相对路径或完整 URL
        :param params: URL 查询参数
        :param data: 表单或原始请求体（application/x-www-form-urlencoded）
        :param json: JSON 请求体（application/json）
        :param files: 文件上传
        :param headers: 自定义请求头（会与 Session Header 合并）
        :param timeout: 单次请求超时（覆盖默认值）
        :param allow_redirects: 是否跟随重定向
        """
        method = method.upper()
        full_url = self._build_url(url)

        # 构造 Trace-ID
        trace_id = context.get("trace_id") or str(uuid.uuid4())
        context.set("trace_id", trace_id)

        # 合并 Header
        merged_headers = dict(self.session.headers)
        if headers:
            merged_headers.update(headers)
        merged_headers.setdefault("X-Trace-Id", trace_id)

        # 自动处理 Content-Type（智能推断）
        if json is not None and not any(k.lower() == "content-type" for k in merged_headers):
            merged_headers["Content-Type"] = "application/json; charset=UTF-8"
        elif data is not None and files is None and not any(
            k.lower() == "content-type" for k in merged_headers
        ):
            merged_headers["Content-Type"] = (
                "application/x-www-form-urlencoded; charset=UTF-8"
            )

        # 超时设置
        req_timeout = timeout or self.timeout

        # 日志：请求发出
        logger.info(
            f"[HTTP] {method} {full_url} | trace={trace_id[:8]}"
        )
        if params:
            logger.debug(f"[HTTP] params={params}")
        if json is not None:
            logger.debug(f"[HTTP] json={self._safe_truncate(json)}")
        if data is not None:
            logger.debug(f"[HTTP] data={self._safe_truncate(data)}")

        # 发送请求
        start_time = time.perf_counter()
        try:
            response = self.session.request(
                method=method,
                url=full_url,
                params=params,
                data=data,
                json=json,
                files=files,
                headers=merged_headers,
                timeout=req_timeout,
                allow_redirects=allow_redirects,
                **kwargs,
            )
        except requests.exceptions.ConnectTimeout as e:
            logger.error(f"[HTTP] 连接超时: {full_url} - {e}")
            raise TimeoutError(f"连接超时: {full_url}") from e
        except requests.exceptions.ReadTimeout as e:
            logger.error(f"[HTTP] 读取超时: {full_url} - {e}")
            raise TimeoutError(f"读取超时: {full_url}") from e
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[HTTP] 连接异常: {full_url} - {e}")
            raise NetworkError(f"连接异常: {full_url}") from e
        except requests.exceptions.RequestException as e:
            logger.error(f"[HTTP] 请求异常: {full_url} - {e}")
            raise NetworkError(str(e)) from e

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # 日志：响应返回
        status = response.status_code
        log_level = logging.WARNING if status >= 400 else logging.INFO
        logger.log(
            log_level,
            f"[HTTP] {status} {method} {full_url} | {elapsed_ms}ms | "
            f"trace={trace_id[:8]}",
        )

        # 尝试打印业务码
        try:
            body = response.json()
            biz_code = self._extract_biz_code(body)
            if biz_code is not None:
                logger.debug(f"[HTTP] biz_code={biz_code}")
        except Exception:
            body = None

        # 保存到上下文（供 YAML 提取与断言）
        context.set("last_response", response)
        context.set("last_status_code", status)
        context.set("last_elapsed_ms", elapsed_ms)
        if body is not None:
            context.set("last_json", body)

        return response

    # ============================================================
    # 便捷方法
    # ============================================================
    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> requests.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> requests.Response:
        return self.request("DELETE", url, **kwargs)

    def patch(self, url: str, **kwargs) -> requests.Response:
        return self.request("PATCH", url, **kwargs)

    # ============================================================
    # 内部辅助
    # ============================================================
    def _build_url(self, url: str) -> str:
        """拼接完整 URL"""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not self.base_url:
            raise ValueError(f"base_url 未设置，无法构造请求 URL: {url}")
        if not url.startswith("/"):
            url = "/" + url
        return self.base_url + url

    @staticmethod
    def _safe_truncate(obj: Any, max_len: int = 1000) -> str:
        """安全截断用于日志打印"""
        s = str(obj)
        if len(s) > max_len:
            return s[:max_len] + "...(truncated)"
        return s

    @staticmethod
    def _extract_biz_code(body: Any) -> Optional[Any]:
        """
        从响应体中提取业务码（用于日志）
        兼容电商 (msg_code / error_code) 和物流 (code) 两种结构
        """
        if not isinstance(body, dict):
            return None
        for key in ("msg_code", "code", "error_code"):
            if key in body:
                return body[key]
        return None

    # ============================================================
    # 生命周期
    # ============================================================
    def close(self):
        """关闭 Session，释放连接池"""
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()