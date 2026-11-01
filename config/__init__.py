# -*- coding: utf-8 -*-
"""
config/__init__.py
------------------
配置模块导出入口（API_TEST 框架）

职责：
    1. 暴露全局配置单例 `settings`
    2. 暴露环境枚举与工具方法（便于外部查询当前环境）
    3. 提供配置热重载入口（便于测试脚本动态切换环境）

使用示例：
    from config import settings
    base_url = settings.api.base_url

    from config import EnvType, get_current_env
    print(get_current_env())   # 'test' / 'uat' / 'prod'
"""
import logging

# ============================================================
# 1. 核心：全局配置单例
# ============================================================
from .settings import settings, Settings

# ============================================================
# 2. 环境枚举与工具（与 config/env/__init__.py 对齐）
# ============================================================
from .env import (
    EnvType,
    get_supported_envs,
    get_env_file,
    load_env_config,
)


# ============================================================
# 3. 便捷函数
# ============================================================
def get_current_env() -> str:
    """获取当前生效的环境名（如 test / uat / prod）"""
    return settings.env_name


def is_production() -> bool:
    """判断当前是否生产环境（便于业务代码做安全判断）"""
    return settings.env_name == EnvType.PROD.value


def is_debug() -> bool:
    """判断当前是否 DEBUG 模式"""
    return settings.debug


# ============================================================
# 4. 版本与元信息
# ============================================================
__version__ = "1.0.0"
__framework_name__ = "API_TEST"

# 加载提示日志
_init_logger = logging.getLogger(__name__)
_init_logger.debug(
    f"[config] 已加载配置模块（env={settings.env_name}, "
    f"framework={__framework_name__} v{__version__}）"
)

# ============================================================
# 5. 显式公开范围
# ============================================================
__all__ = [
    # 核心配置
    "settings",
    "Settings",
    # 环境枚举与工具
    "EnvType",
    "get_supported_envs",
    "get_env_file",
    "load_env_config",
    # 便捷函数
    "get_current_env",
    "is_production",
    "is_debug",
    # 元信息
    "__version__",
    "__framework_name__",
]