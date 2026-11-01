# -*- coding: utf-8 -*-
"""
core/cleanup/__init__.py
------------------------
数据回收站模块导出入口（API_TEST 框架）

聚合导出：
    - TrashBin：数据回收站（核心类）
    - ResourceType：资源类型枚举（user / order / carrier 等）
    - get_trash_bin：全局回收站单例获取函数

使用示例：
    from core.cleanup import TrashBin, ResourceType

    # 登记资源
    TrashBin.register("user", user_id=123)
    TrashBin.register("order", order_no="DD20260105001", user_id=123)

    # 会话结束时统一清理（由 pytest_plugins 自动调用）
    TrashBin.cleanup_all()
"""

from .trash_bin import TrashBin, ResourceType, get_trash_bin

__version__ = "1.0.0"

__all__ = [
    "TrashBin",
    "ResourceType",
    "get_trash_bin",
]