# -*- coding: utf-8 -*-
"""
config/settings.py
------------------
全局配置加载器（Settings）

职责：
    1. 根据环境变量 API_TEST_ENV 加载对应的环境 YAML
    2. 支持 YAML + 环境变量双向覆盖（环境变量优先）
    3. 递归替换 __FROM_CREDENTIALS__ 占位符
    4. 暴露类型安全的全局单例 settings
    5. 支持点号路径读取（settings.get("api.base_url")）

用法：
    from config import settings
    print(settings.api.base_url)
    print(settings.get("api.timeout"))
    print(settings.env_name)   # 'test' / 'uat' / 'prod'
"""
import os
import logging
from typing import Any, Dict, Optional

from common.utils import FileUtil

logger = logging.getLogger(__name__)


# ============================================================
# 通用"点号字典"：支持 settings.api.base_url 访问
# ============================================================
class DotDict:
    """
    将 dict 转为对象属性访问

    特性：
        - settings.api.base_url      等价 settings["api"]["base_url"]
        - settings.get("api.base_url") 支持点号路径
        - 缺失键返回 None（不抛 KeyError）
    """

    def __init__(self, data: Dict[str, Any]):
        for k, v in data.items():
            if isinstance(v, dict):
                setattr(self, k, DotDict(v))
            elif isinstance(v, list):
                # 列表元素如果是 dict，也递归转换
                setattr(self, k, [
                    DotDict(i) if isinstance(i, dict) else i for i in v
                ])
            else:
                setattr(self, k, v)

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        支持点号路径读取

        :param key_path: "api.base_url" / "database.host"
        """
        current: Any = self
        for key in key_path.split("."):
            if isinstance(current, DotDict):
                current = getattr(current, key, None)
            elif isinstance(current, dict):
                current = current.get(key, None)
            else:
                return default
            if current is None:
                return default
        return current

    def to_dict(self) -> Dict[str, Any]:
        """转回普通 dict"""
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, DotDict):
                result[k] = v.to_dict()
            elif isinstance(v, list):
                result[k] = [
                    i.to_dict() if isinstance(i, DotDict) else i for i in v
                ]
            else:
                result[k] = v
        return result

    def __repr__(self) -> str:
        keys = list(self.__dict__.keys())
        return f"<DotDict keys={keys}>"


# ============================================================
# 配置类
# ============================================================
class Settings:
    """
    全局配置加载器

    加载优先级（低 → 高）：
        1. 环境 YAML 默认值
        2. 环境变量 API_TEST_{ENV}_{KEY}（如 API_TEST_TEST_DB_PASSWORD）
        3. __FROM_CREDENTIALS__ 占位符替换（从环境变量读取）
    """

    # 环境变量前缀
    ENV_PREFIX = "API_TEST"

    # 支持的环境列表
    SUPPORTED_ENVS = ("local", "test", "uat", "prod")

    # 默认环境
    DEFAULT_ENV = "test"

    def __init__(self, env: Optional[str] = None):
        # 1. 确定当前环境
        self.env_name = (
                env
                or os.getenv(f"{self.ENV_PREFIX}_ENV")
                or self.DEFAULT_ENV
        ).lower()

        if self.env_name not in self.SUPPORTED_ENVS:
            raise ValueError(
                f"不支持的环境: {self.env_name}，"
                f"可选: {self.SUPPORTED_ENVS}"
            )

        # 2. 加载环境 YAML
        yaml_path = f"config/env/{self.env_name}.yaml"
        raw = FileUtil.load_yaml(yaml_path)
        if raw is None:
            raise FileNotFoundError(
                f"环境配置为空: {yaml_path}"
            )

        # 3. 环境变量覆盖
        raw = self._merge_env_vars(raw)

        # 4. 替换 __FROM_CREDENTIALS__
        raw = self._replace_credentials(raw)

        # 5. 转为 DotDict 挂载到 self
        self._raw = raw
        self._dotted = DotDict(raw)
        self._mount_all()

        logger.info(
            f"[Settings] 环境加载完成: {self.env_name}, "
            f"base_url={self.api.base_url}"
        )

    # ------------------------------------------------------------
    # 挂载所有一级键到 self
    # ------------------------------------------------------------
    def _mount_all(self):
        """把 raw 的每个顶级 key 挂到 self 上"""
        for k, v in self.__dict__["_dotted"].__dict__.items():
            setattr(self, k, v)

    # ------------------------------------------------------------
    # 环境变量覆盖
    # ------------------------------------------------------------
    def _merge_env_vars(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        用环境变量覆盖 YAML 中的配置

        规则：
            YAML: database.password
            ENV:  API_TEST_{ENV}_DATABASE_PASSWORD
            例：API_TEST_TEST_DATABASE_PASSWORD

        只覆盖字符串/数字/布尔，不覆盖复杂嵌套结构（保持 yaml 完整）
        """
        prefix = f"{self.ENV_PREFIX}_{self.env_name.upper()}_"

        for env_key, env_val in os.environ.items():
            if not env_key.startswith(prefix):
                continue

            # API_TEST_TEST_DATABASE_PASSWORD → database.password
            field_path = env_key[len(prefix):].lower().replace("__", ".")

            # 转换值的类型
            env_val = self._cast_value(env_val)

            # 写入 data（支持嵌套）
            self._set_nested(data, field_path.split("_"), env_val)

        return data

    def _set_nested(self, data: Dict, path: list, value: Any):
        """
        按路径写入嵌套字典
        path 如 ["database", "password"]
        """
        # 尝试将下划线合并回正确的 key（如 db_name 是单 key 而非 db.name）
        # 这里简化处理：优先按完整 key 匹配
        if len(path) == 1:
            data[path[0]] = value
            return

        # 尝试贪心匹配：先尝试完整 key，再尝试拆分
        for i in range(len(path), 0, -1):
            candidate = "_".join(path[:i])
            if candidate in data:
                if i == len(path):
                    # 完整匹配
                    data[candidate] = value
                    return
                elif isinstance(data[candidate], dict):
                    # 部分匹配，递归处理剩余
                    self._set_nested(
                        data[candidate], path[i:], value
                    )
                    return

        # 没有匹配，按路径创建
        self._set_nested_recursive(data, path, value)

    def _set_nested_recursive(self, data: Dict, path: list, value: Any):
        """按路径递归创建"""
        current = data
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = value

    # ------------------------------------------------------------
    # 值类型转换
    # ------------------------------------------------------------
    @staticmethod
    def _cast_value(val: str) -> Any:
        """将环境变量字符串转为合适的类型"""
        if val.lower() in ("true", "yes", "on"):
            return True
        if val.lower() in ("false", "no", "off"):
            return False
        if val == "" or val.lower() == "none":
            return None

        # 尝试整数
        try:
            return int(val)
        except ValueError:
            pass

        # 尝试浮点数
        try:
            return float(val)
        except ValueError:
            pass

        # 保留字符串
        return val

    # ------------------------------------------------------------
    # __FROM_CREDENTIALS__ 替换
    # ------------------------------------------------------------
    def _replace_credentials(self, data: Any) -> Any:
        """
        递归替换 __FROM_CREDENTIALS__ 占位符

        规则：
            YAML: database.password: "__FROM_CREDENTIALS__"
            从环境变量读取: API_TEST_{ENV}_DATABASE_PASSWORD
            如果找不到，保持占位符并警告
        """
        if isinstance(data, dict):
            return {
                k: self._replace_credentials_in_value(v, k)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._replace_credentials(i) for i in data]
        return data

    def _replace_credentials_in_value(self, value: Any, key: str) -> Any:
        """处理单个值"""
        if isinstance(value, (dict, list)):
            return self._replace_credentials(value)

        if value == "__FROM_CREDENTIALS__":
            env_key = f"{self.ENV_PREFIX}_{self.env_name.upper()}_{key.upper()}"
            env_val = os.getenv(env_key)

            if env_val:
                logger.debug(f"[Settings] 从环境变量注入: {env_key}")
                return env_val

            # 尝试不加前缀的 key
            env_val = os.getenv(key.upper())
            if env_val:
                return env_val

            logger.warning(
                f"[Settings] 占位符未替换: {key}（环境变量 {env_key} 未设置）"
            )
            return value

        return value

    # ------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------
    def get(self, key_path: str, default: Any = None) -> Any:
        """按点号路径获取配置"""
        return self._dotted.get(key_path, default)

    def is_production(self) -> bool:
        """是否生产环境"""
        return self.env_name == "prod"

    def to_dict(self) -> Dict[str, Any]:
        """导出完整配置字典（调试用）"""
        return self._dotted.to_dict()

    def __repr__(self) -> str:
        return (
            f"<Settings env={self.env_name} "
            f"base_url={self.get('api.base_url')}>"
        )


# ============================================================
# 全局单例
# ============================================================
settings = Settings()