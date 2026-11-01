# -*- coding: utf-8 -*-
"""
core/mock/mock_server.py
------------------------
Mock HTTP 服务（MockServer）

职责：
    1. 在本地启动一个真实的 HTTP 服务（基于标准库 http.server）
    2. 并发接收请求（ThreadingHTTPServer）
    3. 使用 MockRule 匹配请求，返回渲染后的响应
    4. 支持动态延迟（模拟网络超时）
    5. 支持优雅启停，端口占用检测

依赖：
    无第三方依赖（仅用 Python 标准库）
"""
import time
import json
import socket
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from .mock_loader import MockRule, MockLoader

logger = logging.getLogger(__name__)


# ============================================================
# 请求处理器
# ============================================================
class MockRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP 请求处理器（绑定到 MockServer）

    实际逻辑：读取请求 → 找匹配规则 → 渲染响应 → 返回
    """

    # 类变量，绑定到当前 MockServer 实例（由 MockServer 注入）
    server_instance: "MockServer" = None

    # ---------- 屏蔽默认日志（由 MockServer 统一记录） ----------
    def log_message(self, format, *args):
        # 拦截默认的 stderr 日志，避免污染测试日志
        if self.server_instance and self.server_instance._verbose:
            logger.debug(f"[MockServer] {format % args}")

    # ---------- 处理各类 HTTP 方法 ----------
    def do_GET(self):
        self._handle_request("GET")

    def do_POST(self):
        self._handle_request("POST")

    def do_PUT(self):
        self._handle_request("PUT")

    def do_DELETE(self):
        self._handle_request("DELETE")

    def do_PATCH(self):
        self._handle_request("PATCH")

    # ---------- 核心处理逻辑 ----------
    def _handle_request(self, method: str):
        # 1. 解析 URL
        parsed = urllib.parse.urlparse(self.path)
        request_path = parsed.path
        request_query = urllib.parse.parse_qs(parsed.query)

        # 2. 读取请求体
        body_bytes = b""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body_bytes = self.rfile.read(content_length)

        request_body = self._parse_body(body_bytes)

        logger.info(
            f"[MockServer] 📥 {method} {request_path}"
            + (f" | query={request_query}" if request_query else "")
        )

        # 3. 匹配规则
        server = self.server_instance
        if not server:
            self._send_error(500, "MockServer 未初始化")
            return

        matched_rule = server.match_rule(
            method=method,
            path=request_path,
            query=request_query,
            body=request_body,
        )

        if not matched_rule:
            logger.warning(f"[MockServer] 🚫 未匹配任何规则: {method} {request_path}")
            self._send_error(404, f"未匹配 Mock 规则: {method} {request_path}")
            return

        # 4. 更新命中计数
        matched_rule.hit_count += 1

        # 5. 渲染响应（动态变量）
        response_data = matched_rule.render_response(
            request_path=request_path,
            request_query=request_query,
            request_body=request_body,
        )

        # 6. 延迟模拟（超时场景）
        delay = response_data.get("delay", 0.0)
        if delay > 0:
            logger.info(f"[MockServer] ⏱️ 模拟延迟 {delay}s")
            time.sleep(delay)

        # 7. 返回响应
        self._send_response(
            status=response_data.get("status", 200),
            headers=response_data.get("headers", {}),
            body=response_data.get("body"),
        )

    # ---------- 请求体解析 ----------
    @staticmethod
    def _parse_body(body_bytes: bytes) -> Any:
        """尝试解析 JSON，失败则返回原始字符串"""
        if not body_bytes:
            return {}
        try:
            return json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                return body_bytes.decode("utf-8", errors="replace")
            except Exception:
                return body_bytes

    # ---------- 响应发送 ----------
    def _send_response(
            self,
            status: int = 200,
            headers: Optional[Dict[str, str]] = None,
            body: Any = None,
    ):
        """发送 HTTP 响应"""
        self.send_response(status)

        # 默认 JSON 响应
        headers = headers or {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json; charset=utf-8"

        # 内容
        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        elif body is None:
            body_bytes = b""
        else:
            body_bytes = str(body).encode("utf-8")

        headers["Content-Length"] = str(len(body_bytes))

        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()

        if body_bytes:
            self.wfile.write(body_bytes)

    def _send_error(self, status: int, message: str):
        """发送错误响应"""
        self._send_response(
            status=status,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body={"error": message, "status": status},
        )


# ============================================================
# Mock 服务管理器
# ============================================================
class MockServer:
    """
    Mock HTTP 服务管理器

    用法：
        server = MockServer(port=8788)
        server.add_rules(rules)
        server.start()
        # ... 测试 ...
        server.stop()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8788, verbose: bool = True):
        """
        :param host:    监听地址（默认 127.0.0.1）
        :param port:    监听端口（默认 8788）
        :param verbose: 是否打印详细日志
        """
        self.host = host
        self.port = port
        self._verbose = verbose

        self._rules: List[MockRule] = []
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ============================================================
    # 规则管理
    # ============================================================
    def add_rules(self, rules: List[MockRule]) -> "MockServer":
        """添加规则列表"""
        self._rules.extend(rules)
        logger.info(f"[MockServer] 添加 {len(rules)} 条规则（累计 {len(self._rules)} 条）")
        return self

    def add_rule(self, rule: MockRule) -> "MockServer":
        """添加单条规则"""
        self._rules.append(rule)
        return self

    def clear_rules(self) -> None:
        """清空规则"""
        self._rules.clear()

    def match_rule(
            self,
            method: str,
            path: str,
            query: Dict[str, List[str]],
            body: Any,
    ) -> Optional[MockRule]:
        """
        匹配请求

        按规则添加顺序（先注册优先），返回第一个匹配的规则。
        """
        # 过滤 body，如果 body 不是 dict，body_match 无法生效
        body_for_match = body if isinstance(body, dict) else {}

        for rule in self._rules:
            if rule.matches(method, path, query, body_for_match):
                return rule
        return None

    # ============================================================
    # 生命周期管理
    # ============================================================
    def start(self, wait_ready: bool = True, timeout: float = 5.0) -> None:
        """
        启动服务

        :param wait_ready: 是否等待端口就绪（防止竞态）
        :param timeout:    等待超时（秒）
        """
        if self._started:
            logger.warning("[MockServer] 服务已启动，忽略重复启动")
            return

        # 1. 端口可用性检测
        self._check_port_available()

        # 2. 创建服务
        try:
            self._httpd = ThreadingHTTPServer(
                (self.host, self.port),
                MockRequestHandler,
            )
            # 将当前实例注入到 Handler
            MockRequestHandler.server_instance = self
        except OSError as e:
            raise RuntimeError(
                f"[MockServer] 无法绑定 {self.host}:{self.port} - {e}"
            ) from e

        # 3. 后台线程运行
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"MockServer-{self.port}",
            daemon=True,
        )
        self._thread.start()
        self._started = True

        # 4. 等待就绪
        if wait_ready:
            self._wait_until_ready(timeout)

        logger.info(f"[MockServer] 🚀 服务已启动: http://{self.host}:{self.port}")

    def stop(self) -> None:
        """停止服务"""
        if not self._started:
            return

        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception as e:
            logger.warning(f"[MockServer] 关闭时异常: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        self._started = False
        self._httpd = None
        self._thread = None
        logger.info(f"[MockServer] 🛑 服务已停止: {self.port}")

    def is_running(self) -> bool:
        """是否运行中"""
        return self._started and self._httpd is not None

    # ============================================================
    # 内部辅助
    # ============================================================
    def _check_port_available(self) -> None:
        """检测端口是否被占用"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.5)
            result = sock.connect_ex((self.host, self.port))
            if result == 0:
                raise RuntimeError(
                    f"[MockServer] 端口 {self.port} 已被占用，"
                    f"请检查是否有其他 Mock 服务或系统进程占用"
                )
        finally:
            sock.close()

    def _wait_until_ready(self, timeout: float) -> None:
        """等待端口真正可连接"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.2)
                result = sock.connect_ex((self.host, self.port))
                sock.close()
                if result == 0:
                    return
            except Exception:
                pass
            time.sleep(0.05)

        raise RuntimeError(
            f"[MockServer] 启动超时（{timeout}s），端口 {self.port} 未就绪"
        )

    # ============================================================
    # 上下文管理（支持 with 语法）
    # ============================================================
    def __enter__(self) -> "MockServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def __repr__(self) -> str:
        status = "running" if self.is_running() else "stopped"
        return f"<MockServer {self.host}:{self.port} {status} rules={len(self._rules)}>"