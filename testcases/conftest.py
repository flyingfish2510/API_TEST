# -*- coding: utf-8 -*-
"""
testcases/conftest.py
---------------------
Pytest 全局配置与 Fixture

职责：
    1. 注册自定义插件（使 Pytest 识别 .yaml）
    2. 提供全局 Fixture（api_client / auth_manager / data_factory）
    3. 管理 Session 生命周期（前置检查 / 后置清理）
    4. 配置命令行参数（--env / --module / --tag / --workers 等）

注意：
    本文件只负责 Pytest 层面的装配，
    具体执行逻辑由 core.engine.YamlRunner 负责。
"""
import os
import sys
import logging
import pytest
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 注册自定义插件（让 Pytest 识别 YAML）
pytest_plugins = [
    "core.plugins.pytest_plugins",
    "core.plugins.allure_plugins",
]

# 导入框架模块
from config import settings, get_current_env
from common.client import HttpClient, AuthManager
from common.context import context
from core.cleanup import TrashBin

logger = logging.getLogger(__name__)


# ============================================================
# Pytest 命令行参数
# ============================================================
def pytest_addoption(parser):
    """注册自定义命令行参数"""
    group = parser.getgroup("api-test", "API_TEST 框架参数")

    group.addoption(
        "--env",
        action="store",
        default=os.getenv("API_TEST_ENV", "test"),
        help="执行环境（local/test/uat/prod）",
    )
    group.addoption(
        "--module",
        action="store",
        default=None,
        help="只执行指定模块（ecommerce/logistics/smoke）",
    )
    group.addoption(
        "--workers",
        action="store",
        default="1",
        help="并发进程数（对应 pytest-xdist 的 -n）",
    )
    group.addoption(
        "--retry",
        action="store",
        default="0",
        help="失败重试次数",
    )
    group.addoption(
        "--readonly",
        action="store_true",
        default=False,
        help="只读模式（生产巡检用）",
    )


def pytest_configure(config):
    """Pytest 启动时的配置初始化（在插件 configure 之前）"""
    # 1. 处理 --env 参数（切换环境）
    env = config.getoption("--env")
    if env:
        os.environ["API_TEST_ENV"] = env
        # 强制重新加载配置
        import importlib
        import config as config_module
        importlib.reload(config_module)
        # 更新全局 settings
        from config.settings import Settings
        new_settings = Settings()
        for k, v in new_settings.__dict__.items():
            if not k.startswith("_"):
                setattr(settings, k, v)

    # 2. 处理 --readonly 参数
    if config.getoption("--readonly"):
        os.environ["API_TEST_READONLY"] = "true"

    # 3. 处理 --workers（通过 pytest-xdist）
    workers = config.getoption("--workers")
    if workers and workers != "1":
        # 动态设置 xdist 的 numprocesses
        try:
            config.option.numprocesses = int(workers)
        except (ValueError, AttributeError):
            pass

    # 4. 处理 --retry（通过 pytest-rerunfailures）
    retry = config.getoption("--retry")
    if retry and retry != "0":
        try:
            config.option.reruns = int(retry)
        except (ValueError, AttributeError):
            pass

    # 5. 打印启动信息
    logger.info("=" * 60)
    logger.info(f"🚀 API_TEST 框架启动")
    logger.info(f"   环境:   {get_current_env()}")
    logger.info(f"   模块:   {config.getoption('--module') or 'all'}")
    logger.info(f"   并发:   {workers}")
    logger.info(f"   重试:   {retry}")
    logger.info(f"   只读:   {config.getoption('--readonly')}")
    logger.info("=" * 60)


# ============================================================
# Session 级 Fixture（整个测试会话共享）
# ============================================================
@pytest.fixture(scope="session")
def env_name() -> str:
    """当前环境名"""
    return get_current_env()


@pytest.fixture(scope="session")
def api_client() -> HttpClient:
    """全局 HTTP 客户端（复用 Session，减少 TCP 握手）"""
    logger.info("[Fixture] 初始化 HttpClient")

    client = HttpClient(
        base_url=settings.get("api.base_url"),
        timeout=settings.get("api.timeout", 15),
        verify_ssl=settings.get("api.verify_ssl", False),
        retry_times=settings.get("api.retry_times", 2),
    )

    yield client

    # 清理
    client.close()
    logger.info("[Fixture] HttpClient 已关闭")


@pytest.fixture(scope="session")
def auth_manager(api_client) -> AuthManager:
    """全局认证管理器"""
    logger.info("[Fixture] 初始化 AuthManager")

    manager = AuthManager(api_client, env=get_current_env())

    yield manager

    # 清理缓存
    manager.clear_cache()
    logger.info("[Fixture] AuthManager 已清理")


@pytest.fixture(scope="session", autouse=True)
def health_check():
    """Session 启动前检查环境健康（失败立即中断）"""
    logger.info("[Session] 环境健康检查...")

    try:
        import requests
        base_url = settings.get("api.base_url")
        resp = requests.get(base_url, timeout=10, verify=False)
        if resp.status_code >= 500:
            pytest.exit(f"环境不健康: {base_url} 返回 {resp.status_code}", returncode=3)
        logger.info(f"[Session] 环境健康: {base_url} (HTTP {resp.status_code})")
    except Exception as e:
        logger.warning(f"[Session] 环境健康检查失败: {e}")

    yield


# ============================================================
# Function 级 Fixture（每个用例独立）
# ============================================================
@pytest.fixture
def context_sandbox():
    """用例级上下文（自动清理）"""
    # 用例开始
    context.push("fixture_case")

    yield context

    # 用例结束
    context.pop()


@pytest.fixture
def data_factory(api_client):
    """
    测试数据工厂（构造测试数据 + 自动回收）

    说明：
        框架的造数能力由 TrashBin（数据回收站）+ YAML 的 variables: 提供。
        此 fixture 以 TrashBin 为基础，提供统一的「登记造数 → 用例结束清理」入口，
        供 Python 编写的高级用例使用；YAML 用例无需依赖本 fixture。

    用法：
        def test_xxx(data_factory):
            uid = create_user()              # 自行造数
            data_factory.register("user", uid)  # 登记，用例结束后自动清理
    """
    class _DataFactory:
        def __init__(self, client):
            self.client = client

        def register(self, resource_type, resource_id, context=None):
            """登记造数资源，用例结束后由 TrashBin 统一回收"""
            TrashBin.register(resource_type, resource_id, context or {})

        def cleanup(self):
            """立即清理本工厂登记的资源（可选，Session 结束也会统一清理）"""
            TrashBin.cleanup_all(write_report=False)

    factory = _DataFactory(api_client)

    yield factory

    # 用例结束：触发一次清理（已清理过的资源不会重复处理）
    try:
        TrashBin.cleanup_all(write_report=False)
    except Exception as e:
        logger.warning(f"[Fixture] data_factory 清理异常: {e}")


@pytest.fixture
def trash_bin():
    """数据回收站（全局共享）"""
    return TrashBin


# ============================================================
# Session 结束清理
# ============================================================
def pytest_sessionfinish(session, exitstatus):
    """Session 结束时调用（在 pytest_plugins.py 的同名 hook 之前）"""
    logger.info("=" * 60)
    logger.info(f"🏁 Session 结束 | 退出码: {exitstatus}")

    # TrashBin 清理（如果还没被 pytest_plugins 清理）
    try:
        TrashBin.cleanup_all(write_report=True)
    except Exception as e:
        logger.warning(f"[Session] TrashBin 清理异常: {e}")

    logger.info("=" * 60)


# ============================================================
# 失败时的重试与分类
# ============================================================
def pytest_runtest_makereport(item, call):
    """用例结果报告（分类 Failed / Broken / Skipped）"""
    if call.when != "call":
        return

    # 由 core.plugins.pytest_plugins 统一处理
    pass


# ============================================================
# 标记注册（防止 Pytest 警告）
# ============================================================
def pytest_collection_modifyitems(config, items):
    """用例收集后的额外处理"""
    # 过滤模块
    module = config.getoption("--module")
    if module:
        filtered = []
        for item in items:
            if module in str(item.fspath):
                filtered.append(item)
        items[:] = filtered
        logger.info(f"[Collection] 按模块过滤后: {len(items)} 个用例")