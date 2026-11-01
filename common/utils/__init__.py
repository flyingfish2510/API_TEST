# -*- coding: utf-8 -*-
"""
common/utils/__init__.py
------------------------
公共工具模块导出入口

聚合导出：
    - JsonPathUtil:      用于从响应 JSON 中提取数据
    - FileUtil:          用于读写 YAML / JSON / 文本文件
    - TimeUtil:          用于时间处理、时间戳生成
    - CryptoUtil:        用于加解密、哈希计算
    - SnapshotUtil:      用于基线快照的读写与对比
"""

# 1. JSONPath 处理
from .jsonpath_util import JsonPathUtil

# 2. 文件读写
from .file_util import FileUtil

# 3. 时间处理
from .time_util import TimeUtil

# 4. 加解密（文档中提到的承运商密码 MD5 加密场景会用到）
from .crypto_util import CryptoUtil

# 5. 基线快照（用于建议三中的快照对比）
from .snapshot_util import SnapshotUtil

__version__ = "1.0.0"

# 限定 `from common.utils import *` 时暴露的内容
__all__ = [
    "JsonPathUtil",
    "FileUtil",
    "TimeUtil",
    "CryptoUtil",
    "SnapshotUtil",
]