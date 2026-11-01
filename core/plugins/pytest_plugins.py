# -*- coding: utf-8 -*-
"""
core/plugins/pytest_plugins.py
------------------------------
Pytest 自定义收集插件（YAML 用例）

职责：
    1. 让 pytest 识别 testcases/ 下的 .yaml/.yml 文件
    2. 为每个 YAML 文件生成一个 YamlItem
    3. 复用 conftest.py 提供的 Session 级 Fixture（api_client / auth_manager），
       避免重复创建 HttpClient、避免鉴权态丢失
    4. 统一维护上下文压栈/出栈与客户端生命周期
"""
import logging
import pytest

logger = logging.getLogger(__name__)


# ============================================================
# 全局 Hook
# ============================================================
def pytest_configure(config):
    """Pytest 启动时"""
    logger.info("[pytest_plugins] 已加载")


def pytest_sessionfinish(session, exitstatus):
    """Session 结束"""
    logger.info(f"[pytest_plugins] Session 结束，退出码: {exitstatus}")


def pytest_runtest_makereport(item, call):
    """用例结果报告（占位）"""
    return None


def pytest_collect_file(parent, file_path):
    """识别 testcases/ 下的 .yaml 文件"""
    if file_path.suffix in (".yaml", ".yml"):
        path_str = str(file_path).replace("\\", "/")
        if "testcases/" in path_str and "/config/" not in path_str:
            return YamlFile.from_parent(parent, path=file_path)
    return None


# ============================================================
# 自定义收集器
# ============================================================
class YamlFile(pytest.File):
    """YAML 用例文件收集器"""

    def collect(self):
        # 每个 YAML 文件生成一个测试项
        yield YamlItem.from_parent(self, name=self.path.stem)


class YamlItem(pytest.Item):
    """
    YAML 用例执行器

    关键设计：
        - 通过 self._request 拿到 conftest.py 定义的 Session 级 fixture，
          复用同一个 HttpClient 与 AuthManager（不再另起炉灶新建客户端）
        - 执行完成后，把 session 中的 cookie 回写到全局 context，供后续用例复用
    """
    def setup(self):
        """获取 Session 级依赖（懒加载，避免重复创建）"""
        # pytest.Item 没有 _request，需通过 session 的 fixture 管理器拿 Session 级 fixture
        fm = self.session._fixturemanager
        self._api_client = self._get_session_fixture("api_client", fm)
        self._auth_manager = self._get_session_fixture("auth_manager", fm)

        if self._api_client is None:
            # 兜底：极端情况下（fixture 未注册）自行创建，保证单文件可直接执行
            from common.client import HttpClient
            from config import settings
            self._api_client = HttpClient(base_url=settings.get("api.base_url"))

    def _get_session_fixture(self, name, fixturemanager):
        """从 Session 作用域解析并实例化一个 fixture（不存在则返回 None）"""
        from _pytest.fixtures import FixtureDef  # noqa: F401
        try:
            # 在 item 自身作用域查找 fixturedef
            fixturedefs = fixturemanager.getfixturedefs(name, self.nodeid)
            if not fixturedefs:
                fixturedefs = fixturemanager.getfixturedefs(name, self.parent.nodeid)
            if not fixturedefs:
                return None
            fixturedef = fixturedefs[-1]
            # 通过 request 机制实例化：借用 session 的 _request 不通用，
            # 此处直接调用 fixture 函数（仅适用于无参数或仅依赖已存在 fixture 的简单情况）
            # 更稳妥：用 FixtureRequest
            from _pytest.fixtures import FixtureRequest
            request = FixtureRequest(self, _ispytest=True)
            return request.getfixturevalue(name)
        except Exception as e:
            logger.debug(f"[YamlItem] 解析 Session fixture '{name}' 失败: {e}")
            return None

    def runtest(self):
        from core.engine import YamlLoader, YamlRunner
        from common.context import context

        # 1. 加载 YAML
        case_data = YamlLoader.load(str(self.path))

        # 2. 复用 Session 级 HttpClient（而非新建）
        client = self._api_client

        # 3. 上下文压栈（保证 case 作用域隔离）
        context.push(self.name)

        try:
            # 4. 执行
            YamlRunner.run_case(case_data, client)
        finally:
            # 5. 持久化后续用例可复用的全局态（token / cookies）
            self._persist_global_state(client)
            # 6. 上下文出栈
            context.pop()

    def _persist_global_state(self, client):
        """
        将 session 中已建立的登录态写回全局上下文，
        使后续用例的 _get_global_headers() 能自动带上鉴权信息。
        """
        from common.context import context

        try:
            # Cookie → 全局 cookies
            session_cookies = dict(getattr(client.session, "cookies", {}) or {})
            if session_cookies:
                context.set("cookies", session_cookies, scope="global")

            # Header 中的 token → 全局 token
            headers = getattr(client.session, "headers", {}) or {}
            token = headers.get("token") or headers.get("Token")
            if token:
                context.set("token", token, scope="global")
        except Exception as e:
            logger.debug(f"[YamlItem] 持久化全局登录态失败（忽略）: {e}")

    def reportinfo(self):
        return self.path, 0, f"YAML: {self.name}"