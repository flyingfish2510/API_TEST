# -*- coding: utf-8 -*-
"""
config/env/__init__.py
----------------------
环境配置模块导出入口

职责：
    1. 定义受支持的环境枚举
    2. 提供环境配置文件的路径定位
    3. 提供加载指定环境配置的统一方法（委托给 FileUtil）
"""

import os
from enum import Enum
from typing import Any, Dict, List

from common.utils import FileUtil


class EnvType(str, Enum):
    """受支持的环境枚举（继承 str，便于直接比较）"""
    LOCAL = "local"
    TEST = "test"
    UAT = "uat"
    PROD = "prod"


# 当前模块所在目录（即 config/env）
_ENV_DIR = os.path.dirname(os.path.abspath(__file__))


def get_supported_envs() -> List[str]:
    """获取所有受支持的环境名列表"""
    return [e.value for e in EnvType]


def get_env_file(env_name: str) -> str:
    """
    获取指定环境配置文件的绝对路径

    :param env_name: 环境名（local/test/uat/prod）
    :return:         yaml 文件绝对路径
    :raises ValueError: 环境名非法
    """
    env_name = (env_name or "").lower().strip()
    if env_name not in get_supported_envs():
        raise ValueError(
            f"不支持的环境: {env_name}，可选: {get_supported_envs()}"
        )
    return os.path.join(_ENV_DIR, f"{env_name}.yaml")


def load_env_config(env_name: str) -> Dict[str, Any]:
    """
    加载指定环境的配置

    :param env_name: 环境名
    :return:         配置字典
    :raises FileNotFoundError: 配置文件不存在
    """
    path = get_env_file(env_name)
    return FileUtil.load_yaml(path)


__version__ = "1.0.0"

__all__ = [
    "EnvType",
    "get_supported_envs",
    "get_env_file",
    "load_env_config",
]