# -*- coding: utf-8 -*-
"""
core/plugins/__init__.py
------------------------
pytest 插件包。

注意：
    这里刻意【不】在包初始化时 eager-import allure_plugins，
    否则 pytest 在处理 pytest_plugins 列表时会对已被导入的
    core.plugins.allure_plugins 触发 PytestAssertRewriteWarning。
    因此对 allure_plugins 的符号采用惰性（__getattr__）方式加载。
"""

from .pytest_plugins import (
    YamlFile,
    YamlItem,
    pytest_collect_file,
    pytest_configure,
    pytest_sessionfinish,
    pytest_runtest_makereport,
)


def __getattr__(name):
    # 惰性加载 allure_plugins，避免 pytest 插件注册阶段的提前导入告警
    if name in ("attach_request_response", "attach_error", "attach_environment"):
        try:
            from . import allure_plugins as _ap
        except ImportError:
            def _noop(*args, **kwargs):
                return None
            return _noop
        return getattr(_ap, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "YamlFile", "YamlItem",
    "pytest_collect_file", "pytest_configure",
    "pytest_sessionfinish", "pytest_runtest_makereport",
    "attach_request_response", "attach_error", "attach_environment",
]
