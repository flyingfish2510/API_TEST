# -*- coding: utf-8 -*-
"""
core/strategy/incremental_runner.py
-----------------------------------
增量执行策略（IncrementalRunner）

职责：
    1. 读取 Git Diff 产生的变更文件列表（changed_files.txt）
    2. 根据映射规则，计算本次需要执行的 YAML 用例
    3. 无法匹配时，退化为 smoke 标签
    4. 生成 incremental-plan.json 供 CI 消费

映射规则分为两种：
    - 精确匹配：代码目录 → 用例目录（如 logistics/ → testcases/logistics/）
    - 关键词匹配：文件名关键词 → YAML 文件名（如 order_pay → order_flow.yaml）
"""
import os
import re
import json
import logging
from typing import Any, Dict, List, Optional

from common.utils import FileUtil
from common.exceptions import IncrementalPlanError

logger = logging.getLogger(__name__)


# ============================================================
# 模块映射规则（可扩展）
# ============================================================
class ModuleMapping:
    """
    代码路径 / 关键词 → 测试用例的映射规则

    规则匹配优先级：
        1. 精确路径前缀匹配（如 logistics/）
        2. 关键词包含匹配（如 payment / order_pay）
        3. 全局变更 → 全量 smoke
    """

    # 精确路径映射（代码目录 → 用例目录）
    PATH_MAPPINGS: Dict[str, str] = {
        "testcases/ecommerce/": "testcases/ecommerce/",
        "testcases/logistics/": "testcases/logistics/",
        "common/client/auth": "testcases/ecommerce/user_login.yaml,testcases/logistics/login.yaml",
        "core/engine/": "smoke",  # 引擎改动 → 全量 smoke
        "common/": "smoke",  # 公共库改动 → 全量 smoke
        "config/": "smoke",  # 配置改动 → 全量 smoke
    }

    # 关键词映射（代码路径中的关键词 → YAML 文件名关键词）
    KEYWORD_MAPPINGS: Dict[str, List[str]] = {
        "order": ["order_flow", "order_create", "order_cancel", "order_pay", "order_query"],
        "pay": ["order_flow", "order_pay"],
        "cart": ["shopping_cart", "del_cart"],
        "goods": ["goods_list", "goods_detail"],
        "user": ["user_login", "user_crud", "user_create", "user_query", "user_update", "user_delete"],
        "carrier": ["carrier_crud"],
        "dispatch": ["dispatch_flow"],
        "logistics": ["dispatch_flow", "check_logistics", "inventory_push", "cost_bill"],
        "inventory": ["check_inventory", "inventory_push"],
        "cost": ["cost_bill"],
    }

    # 全局变更关键词（改这些就当全量 smoke）
    GLOBAL_KEYWORDS: List[str] = [
        "requirements.txt",
        "pytest.ini",
        "conftest.py",
        "core/models/",
        "core/plugins/",
    ]


# ============================================================
# 增量执行引擎
# ============================================================
class IncrementalRunner:
    """
    增量执行引擎（静态方法）
    """

    # 默认测试用例根目录
    TESTCASES_ROOT = "testcases"

    # 默认退化模式：冒烟
    FALLBACK_TAG = "smoke"

    # ============================================================
    # 主入口：计算执行计划
    # ============================================================
    @classmethod
    def calculate_plan(
            cls,
            changed_files_path: str,
            testcases_root: Optional[str] = None,
            output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        根据变更文件列表计算执行计划

        :param changed_files_path: 变更文件列表路径（由 git diff 生成）
        :param testcases_root:     测试用例根目录
        :param output_path:        计划输出路径（可选）
        :return:                   执行计划字典：
            {
                "mode": "incremental" / "smoke" / "fallback",
                "files": ["testcases/ecommerce/order_flow.yaml"],
                "reason": "...",
                "changed_files": [...],
            }
        """
        testcases_root = testcases_root or cls.TESTCASES_ROOT

        # 1. 读取变更文件
        changed_files = cls._read_changed_files(changed_files_path)
        if not changed_files:
            logger.info("[Incremental] 无变更文件，退化为 smoke")
            plan = cls._build_smoke_plan(
                reason="无变更文件列表",
                changed_files=[],
            )
            return cls._save_plan(plan, output_path)

        logger.info(f"[Incremental] 检测到 {len(changed_files)} 个变更文件")

        # 2. 检查是否包含全局变更
        if cls._has_global_change(changed_files):
            logger.info("[Incremental] 存在全局变更，退化为 smoke")
            plan = cls._build_smoke_plan(
                reason="包含全局变更（配置文件/引擎/框架核心）",
                changed_files=changed_files,
            )
            return cls._save_plan(plan, output_path)

        # 3. 精确匹配
        matched_files = cls._match_by_path(changed_files, testcases_root)

        # 4. 关键词匹配
        matched_files.extend(cls._match_by_keyword(changed_files, testcases_root))

        # 5. 去重 + 过滤不存在的文件
        matched_files = cls._dedupe_and_filter(matched_files)

        # 6. 结果判定
        if not matched_files:
            logger.info("[Incremental] 无法匹配任何用例，退化为 smoke")
            plan = cls._build_smoke_plan(
                reason="变更文件无法匹配任何用例",
                changed_files=changed_files,
            )
        else:
            logger.info(f"[Incremental] 匹配到 {len(matched_files)} 个 YAML 用例")
            plan = {
                "mode": "incremental",
                "files": matched_files,
                "reason": "根据变更文件精确匹配",
                "changed_files": changed_files,
            }

        return cls._save_plan(plan, output_path)

    # ============================================================
    # 1. 读取变更文件
    # ============================================================
    @staticmethod
    def _read_changed_files(path: str) -> List[str]:
        """读取 git diff 输出的变更文件列表"""
        if not os.path.exists(path):
            logger.warning(f"[Incremental] 变更文件列表不存在: {path}")
            return []

        try:
            content = FileUtil.read_text(path)
            files = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            return files
        except Exception as e:
            logger.warning(f"[Incremental] 读取变更列表失败: {e}")
            return []

    # ============================================================
    # 2. 全局变更检测
    # ============================================================
    @classmethod
    def _has_global_change(cls, changed_files: List[str]) -> bool:
        """判断是否包含全局变更"""
        for file in changed_files:
            for keyword in ModuleMapping.GLOBAL_KEYWORDS:
                if keyword in file:
                    logger.info(f"[Incremental] 命中全局变更关键词: {keyword} (文件: {file})")
                    return True
        return False

    # ============================================================
    # 3. 精确路径匹配
    # ============================================================
    @classmethod
    def _match_by_path(
            cls,
            changed_files: List[str],
            testcases_root: str,
    ) -> List[str]:
        """根据路径前缀精确映射"""
        matched = []

        for file in changed_files:
            for code_prefix, target in ModuleMapping.PATH_MAPPINGS.items():
                if file.startswith(code_prefix):
                    if target == "smoke":
                        # 特殊标记：全量 smoke
                        continue

                    # target 可能是逗号分隔的多个 YAML
                    for yaml_path in target.split(","):
                        yaml_path = yaml_path.strip()
                        if yaml_path:
                            matched.append(yaml_path)
                    break

        return matched

    # ============================================================
    # 4. 关键词匹配
    # ============================================================
    @classmethod
    def _match_by_keyword(
            cls,
            changed_files: List[str],
            testcases_root: str,
    ) -> List[str]:
        """根据文件名关键词匹配 YAML 用例"""
        matched = []
        all_yaml_files = cls._list_all_yaml(testcases_root)

        for file in changed_files:
            file_lower = file.lower()
            for keyword, yaml_keywords in ModuleMapping.KEYWORD_MAPPINGS.items():
                if keyword in file_lower:
                    # 在用例目录里找包含这些关键词的 YAML
                    for yaml_file in all_yaml_files:
                        yaml_name = os.path.basename(yaml_file).lower()
                        if any(kw.lower() in yaml_name for kw in yaml_keywords):
                            matched.append(yaml_file)

        return matched

    # ============================================================
    # 5. 去重与过滤
    # ============================================================
    @staticmethod
    def _dedupe_and_filter(files: List[str]) -> List[str]:
        """去重并过滤不存在的文件"""
        seen = set()
        result = []
        for f in files:
            if f in seen:
                continue
            seen.add(f)
            if os.path.exists(f):
                result.append(f)
            else:
                logger.debug(f"[Incremental] 过滤不存在的文件: {f}")
        return result

    # ============================================================
    # 6. 列出所有 YAML
    # ============================================================
    @staticmethod
    def _list_all_yaml(root: str) -> List[str]:
        """列出根目录下所有 YAML 文件"""
        try:
            return FileUtil.list_files(root, suffix=[".yaml", ".yml"], recursive=True)
        except Exception:
            return []

    # ============================================================
    # 7. 构造 smoke 计划
    # ============================================================
    @classmethod
    def _build_smoke_plan(cls, reason: str, changed_files: List[str]) -> Dict[str, Any]:
        """构造退化到 smoke 的计划"""
        return {
            "mode": "smoke",
            "files": [],  # 由 CI 使用 --tag=smoke 替代
            "tag": cls.FALLBACK_TAG,
            "reason": reason,
            "changed_files": changed_files,
        }

    # ============================================================
    # 8. 保存计划
    # ============================================================
    @staticmethod
    def _save_plan(plan: Dict[str, Any], output_path: Optional[str]) -> Dict[str, Any]:
        """保存计划到文件"""
        if not output_path:
            return plan

        try:
            FileUtil.ensure_dir(output_path)
            FileUtil.dump_json(plan, output_path)
            logger.info(f"[Incremental] 计划已保存: {output_path}")
        except Exception as e:
            logger.warning(f"[Incremental] 计划保存失败: {e}")
        return plan