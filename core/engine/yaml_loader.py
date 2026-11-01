# -*- coding: utf-8 -*-
"""
core/engine/yaml_loader.py
--------------------------
YAML 用例加载器（YamlLoader）

职责：
    1. 扫描指定目录，发现所有 .yaml / .yml 用例文件
    2. 读取并解析 YAML（支持 include 片段复用）
    3. 基础结构校验（必须是 list，必须有 config / test 节）
    4. 提取用例元信息（模块名、优先级、tags）
    5. 提供按模块、按标签过滤的接口

典型目录结构：
    testcases/
    ├── ecommerce/
    │   ├── user_login.yaml
    │   └── order_flow.yaml
    └── logistics/
        ├── order_create.yaml
        └── dispatch_flow.yaml
"""
import os
import logging
from typing import Any, Dict, List, Optional

from common.utils import FileUtil
from common.exceptions import YamlSyntaxError
from config import settings

logger = logging.getLogger(__name__)


class YamlLoader:
    """
    YAML 用例加载器（静态方法）
    """

    # 支持的 YAML 后缀
    YAML_SUFFIXES = (".yaml", ".yml")

    # ============================================================
    # 1. 用例发现
    # ============================================================
    @classmethod
    def discover(
            cls,
            root_dir: str = "testcases",
            module: Optional[str] = None,
    ) -> List[str]:
        """
        扫描目录发现所有 YAML 用例文件

        :param root_dir: 根目录（默认 testcases）
        :param module:   只加载指定模块（如 ecommerce / logistics），None 则全部
        :return:         文件路径列表

        示例：
            >>> YamlLoader.discover("testcases", module="ecommerce")
            ['testcases/ecommerce/user_login.yaml', 'testcases/ecommerce/order_flow.yaml']
        """
        if not os.path.isdir(root_dir):
            logger.warning(f"[YamlLoader] 目录不存在: {root_dir}")
            return []

        search_dir = root_dir
        if module:
            search_dir = os.path.join(root_dir, module)
            if not os.path.isdir(search_dir):
                logger.warning(f"[YamlLoader] 模块目录不存在: {search_dir}")
                return []

        files = FileUtil.list_files(search_dir, suffix=list(cls.YAML_SUFFIXES), recursive=True)
        logger.info(f"[YamlLoader] 发现 {len(files)} 个 YAML 文件（目录: {search_dir}）")
        return files

    # ============================================================
    # 2. 加载与解析
    # ============================================================
    @classmethod
    def load(
            cls,
            file_path: str,
            resolve_include: bool = True,
    ) -> Dict[str, Any]:
        """
        加载单个 YAML 文件，返回结构化数据

        :param file_path:       YAML 文件路径
        :param resolve_include: 是否处理 include（公共片段）
        :return:                解析后的字典：
            {
                "file": "testcases/ecommerce/user_login.yaml",
                "module": "ecommerce",
                "case_name": "用户登录接口测试",
                "priority": "P0",
                "tags": ["smoke", "p0"],
                "config": {...},
                "setup": [...],
                "steps": [...],
                "teardown": [...],
            }
        """
        if not os.path.exists(file_path):
            raise YamlSyntaxError(f"YAML 文件不存在: {file_path}")

        # ---------- 读取原始 YAML ----------
        try:
            raw = FileUtil.load_yaml(file_path)
        except Exception as e:
            raise YamlSyntaxError(f"YAML 文件解析失败: {file_path} - {e}") from e

        if raw is None:
            raise YamlSyntaxError(f"YAML 文件为空: {file_path}")

        # ---------- 结构校验 ----------
        cls._validate_structure(raw, file_path)

        # ---------- 提取元信息 ----------
        config = cls._extract_config(raw, file_path)
        module = cls._extract_module(file_path)

        # ---------- 解析公共片段（可选） ----------
        if resolve_include:
            raw = cls._resolve_includes(raw, os.path.dirname(file_path))

        # ---------- 组装结构化数据 ----------
        # 注意：setup/steps/teardown 统一输出为「扁平的步骤列表」，
        # 每个步骤就是 {"name": ..., "request": {...}} 这种 dict，
        # 不再保留 [{"test": {...}}] 的外层包装，避免引擎二次钻取。
        result = {
            "file": file_path,
            "module": module,
            "case_name": config.get("name", os.path.basename(file_path)),
            "priority": config.get("priority", "P2"),
            "tags": config.get("tags", []),
            "config": config,
            "setup": cls._flatten_phase(raw, "setup"),
            "steps": cls._flatten_phase(raw, "test"),
            "teardown": cls._flatten_phase(raw, "teardown"),
        }

        logger.debug(
            f"[YamlLoader] 加载用例: {result['case_name']} "
            f"({len(result['steps'])} 步)"
        )
        return result

    # ============================================================
    # 3. 批量加载
    # ============================================================
    @classmethod
    def load_all(
            cls,
            root_dir: str = "testcases",
            module: Optional[str] = None,
            tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量加载并过滤

        :param root_dir: 根目录
        :param module:   指定模块
        :param tags:     按标签过滤（如 ["smoke"]）
        :return:         所有符合条件的用例列表
        """
        files = cls.discover(root_dir, module)
        cases = []

        for file_path in files:
            try:
                case = cls.load(file_path)
            except YamlSyntaxError as e:
                # 单个用例加载失败不阻断其他用例
                logger.error(f"[YamlLoader] 加载失败 {file_path}: {e}")
                continue

            # 标签过滤
            if tags:
                case_tags = set(case.get("tags", []))
                if not case_tags.intersection(tags):
                    continue

            cases.append(case)

        logger.info(
            f"[YamlLoader] 加载完成: {len(cases)} / {len(files)} 个用例通过"
            + (f"（标签过滤: {tags}）" if tags else "")
        )
        return cases

    # ============================================================
    # 4. 结构校验
    # ============================================================
    @staticmethod
    def _validate_structure(raw: Any, file_path: str) -> None:
        """校验 YAML 基本结构"""
        if not isinstance(raw, list):
            raise YamlSyntaxError(
                f"YAML 根节点必须是列表（每个元素为 setup/test/teardown）\n"
                f"文件: {file_path}\n"
                f"实际类型: {type(raw).__name__}"
            )

        has_test = False
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise YamlSyntaxError(
                    f"第 {index + 1} 个元素必须是字典，实际: {type(item).__name__}\n"
                    f"文件: {file_path}"
                )

            # 每个元素必须有且只有一个主键：config / setup / test / teardown
            keys = set(item.keys())
            valid_keys = {"config", "setup", "test", "teardown"}
            if not keys.issubset(valid_keys):
                invalid = keys - valid_keys
                raise YamlSyntaxError(
                    f"第 {index + 1} 个元素包含未知主键: {invalid}\n"
                    f"文件: {file_path}，合法主键: {valid_keys}"
                )

            if "test" in keys:
                has_test = True

        if not has_test:
            raise YamlSyntaxError(
                f"YAML 用例必须至少包含一个 'test' 步骤\n文件: {file_path}"
            )

    # ============================================================
    # 5. Config 提取
    # ============================================================
    @staticmethod
    def _extract_config(raw: List[dict], file_path: str) -> Dict[str, Any]:
        """提取 config 节点（必须是第一个元素）"""
        if raw and isinstance(raw[0], dict) and "config" in raw[0]:
            return raw[0]["config"] or {}

        # 允许没有 config，但给默认值
        logger.warning(f"[YamlLoader] 缺少 config 节点: {file_path}")
        return {}

    # ============================================================
    # 6. 模块提取（从路径推断）
    # ============================================================
    @staticmethod
    def _extract_module(file_path: str) -> str:
        """
        从路径提取模块名
        例: testcases/ecommerce/user_login.yaml → ecommerce
        """
        parts = file_path.replace("\\", "/").split("/")
        try:
            idx = parts.index("testcases")
            return parts[idx + 1] if idx + 1 < len(parts) else "unknown"
        except ValueError:
            return "unknown"

    # ============================================================
    # 7. 阶段步骤展平（消除 [{"test": {...}}] 包装）
    # ============================================================
    @classmethod
    def _flatten_phase(cls, raw: List[dict], phase: str) -> List[Dict[str, Any]]:
        """
        将某一阶段（setup / test / teardown）的原始条目展平为标准步骤列表

        兼容多种写法：
            - 标准：  {"test": {"name": ..., "request": {...}}}
            - 嵌套：  {"setup": [{"test": {...}}, {"test": {...}}]}
            - 数组值：{"test": [{...}, {...}]}

        :return: [ {"name": ..., "request": {...}}, ... ]
        """
        steps: List[Dict[str, Any]] = []

        def _collect(value: Any):
            if isinstance(value, list):
                for v in value:
                    _collect(v)
            elif isinstance(value, dict):
                # 命中阶段 key 时取其内容继续展平；否则视为普通步骤
                if phase in value and len(value) == 1:
                    _collect(value[phase])
                else:
                    steps.append(value)

        for item in raw:
            if isinstance(item, dict) and phase in item:
                _collect(item[phase])
            elif phase == "setup" or phase == "teardown":
                # setup/teardown 允许嵌套在其它 key 下（已被 _resolve_includes 处理）
                continue

        return steps

    # ============================================================
    # 8. Include 片段解析（公共步骤复用）
    # ============================================================
    @classmethod
    def _resolve_includes(
            cls,
            raw: List[dict],
            base_dir: str,
    ) -> List[dict]:
        """
        处理 include 语法

        YAML 中可以写：
            - setup:
                - include: "common_fragments/admin_login.yaml"
                - test:
                    name: "额外的 setup"
                    request: {...}

        解析后，include 会被替换为文件中定义的步骤列表。
        """
        result = []

        for item in raw:
            if isinstance(item, dict) and "setup" in item:
                resolved_setup = []
                for step in item.get("setup") or []:
                    resolved_setup.extend(cls._process_step_includes(step, base_dir))
                result.append({"setup": resolved_setup})
            else:
                result.append(item)

        return result

    @classmethod
    def _process_step_includes(cls, step: dict, base_dir: str) -> List[dict]:
        """递归处理单个 step 中的 include"""
        if not isinstance(step, dict):
            return [step]

        if "include" in step:
            include_path = step["include"]
            # 相对于 YAML 文件目录
            full_path = os.path.join(base_dir, include_path)

            # 也尝试相对于 data/fragments
            if not os.path.exists(full_path):
                full_path = os.path.join("data/fragments", include_path)

            if not os.path.exists(full_path):
                raise YamlSyntaxError(
                    f"include 片段不存在: {include_path}\n"
                    f"尝试路径: {base_dir}/{include_path}, data/fragments/{include_path}"
                )

            logger.debug(f"[YamlLoader] 解析 include: {include_path}")
            fragment = FileUtil.load_yaml(full_path)

            if not isinstance(fragment, list):
                raise YamlSyntaxError(
                    f"include 片段必须是列表: {include_path}"
                )

            # 递归处理嵌套 include
            resolved = []
            for sub_step in fragment:
                resolved.extend(cls._process_step_includes(sub_step, os.path.dirname(full_path)))
            return resolved

        # 普通 step，原样返回
        return [step]


# ============================================================
# 内部辅助：判断步骤类型
# ============================================================
def _is_step(item: dict, kind: str) -> bool:
    """判断元素是否属于指定类型（setup / test / teardown）"""
    return isinstance(item, dict) and kind in item