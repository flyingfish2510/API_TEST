# -*- coding: utf-8 -*-
"""
core/engine/__init__.py
-----------------------
核心执行引擎导出入口（API_TEST 框架）

聚合导出：
    - YamlLoader：YAML 文件发现与加载
    - YamlRunner：用例生命周期编排与执行驱动
    - VariableResolver：变量解析（环境、Faker、上下文、函数等）
    - RequestBuilder：HTTP 请求组装（JSON / Form / 文件）
    - Extractor：响应数据提取（JSONPath / 正则 / Header）
    - EnvGuard：环境安全守卫（生产拦截、高危接口审计）

使用示例（由 conftest.py 或 pytest_plugins.py 调用）：
    from core.engine import YamlRunner
    YamlRunner.run_case(yaml_file_path)
"""

from .yaml_loader import YamlLoader
from .yaml_runner import YamlRunner
from .variable_resolver import VariableResolver
from .request_builder import RequestBuilder
from .extractor import Extractor
from .env_guard import EnvGuard

__version__ = "1.0.0"

__all__ = [
    "YamlLoader",
    "YamlRunner",
    "VariableResolver",
    "RequestBuilder",
    "Extractor",
    "EnvGuard",
]