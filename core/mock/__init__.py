# -*- coding: utf-8 -*-
"""
core/mock/__init__.py
---------------------
Mock 服务模块导出入口（API_TEST 框架）

聚合导出：
    - MockServer：本地 Mock HTTP 服务（支持启停、动态规则匹配）
    - MockLoader：Mock 规则加载器（读取 data/mocks/*.json）
    - MockRule：规则数据模型（结构化定义）

使用示例（引擎层自动调用）：
    from core.mock import MockServer, MockLoader

    # 加载规则
    rules = MockLoader.load("payment_gateway.json")

    # 启动服务
    server = MockServer(port=8788)
    server.add_rules(rules)
    server.start()

    # ... 执行测试 ...

    # 停止
    server.stop()
"""

from .mock_server import MockServer, MockRequestHandler
from .mock_loader import MockLoader, MockRule

__version__ = "1.0.0"

__all__ = [
    "MockServer",
    "MockRequestHandler",
    "MockLoader",
    "MockRule",
]