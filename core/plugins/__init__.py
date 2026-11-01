# -*- coding: utf-8 -*-
from .pytest_plugins import (
    YamlFile,
    YamlItem,
    pytest_collect_file,
    pytest_configure,
    pytest_sessionfinish,
    pytest_runtest_makereport,
)

try:
    from .allure_plugins import (
        attach_request_response,
        attach_error,
        attach_environment,
    )
except ImportError:
    def attach_request_response(*args, **kwargs): pass
    def attach_error(*args, **kwargs): pass
    def attach_environment(*args, **kwargs): pass

__all__ = [
    "YamlFile", "YamlItem",
    "pytest_collect_file", "pytest_configure",
    "pytest_sessionfinish", "pytest_runtest_makereport",
    "attach_request_response", "attach_error", "attach_environment",
]