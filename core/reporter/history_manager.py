# -*- coding: utf-8 -*-
"""
core/reporter/history_manager.py
--------------------------------
历史趋势数据管理器（HistoryManager）

职责：
    1. 在生成新报告前，把上一次的 history 目录复制到新的 allure-results
    2. 生成报告后，修剪 history 目录（保留最近 N 次）
    3. 兼容 Jenkins 插件环境（检测到插件管理则跳过）
    4. 支持自定义 history 保留策略

Allure history 工作原理：
    - allure-results/history/  → 存放"上次构建"的历史数据
    - allure-report/history/   → 本次生成的报告会写入 history 子目录
    - 下次构建时，需将 allure-report/history 复制到 allure-results/history
    - Allure 生成时会读取并合并，形成趋势图
"""
import os
import shutil
import logging
from typing import List, Optional

from common.utils import FileUtil
from config import settings

logger = logging.getLogger(__name__)


class HistoryManager:
    """
    历史趋势数据管理器（静态方法）
    """

    # 默认保留最近 N 次构建历史
    DEFAULT_KEEP = 20

    # ============================================================
    # 1. 准备历史（生成报告前调用）
    # ============================================================
    @classmethod
    def prepare_history(
            cls,
            results_dir: str = "reports/allure-results",
            report_dir: str = "reports/allure-report",
            keep: Optional[int] = None,
    ) -> bool:
        """
        在生成新报告前，把上次的 history 复制到 allure-results

        :param results_dir: allure-results 目录（本次写入）
        :param report_dir:  allure-report 目录（上次生成的）
        :param keep:        保留最近 N 次（None 则从配置读取）
        :return:            是否成功复制
        """
        # 1. Jenkins 插件场景跳过
        if cls._is_jenkins_plugin_managed():
            logger.info("[History] Jenkins Allure Plugin 已管理历史，跳过")
            return False

        # 2. 检查源目录
        src_history = os.path.join(report_dir, "history")
        if not os.path.isdir(src_history):
            logger.info(f"[History] 无历史数据（首次构建），跳过: {src_history}")
            return False

        # 3. 准备目标目录
        dst_history = os.path.join(results_dir, "history")
        FileUtil.ensure_dir(results_dir)

        # 若已存在则先清空（避免累积）
        if os.path.exists(dst_history):
            shutil.rmtree(dst_history)
        os.makedirs(dst_history, exist_ok=True)

        # 4. 复制
        try:
            for item in os.listdir(src_history):
                src = os.path.join(src_history, item)
                dst = os.path.join(dst_history, item)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                elif os.path.isdir(src):
                    shutil.copytree(src, dst)
            logger.info(f"[History] 历史已复制: {src_history} → {dst_history}")
            return True
        except Exception as e:
            logger.warning(f"[History] 复制历史失败: {e}")
            return False

    # ============================================================
    # 2. 修剪历史（生成报告后调用）
    # ============================================================
    @classmethod
    def prune_history(
            cls,
            report_dir: str = "reports/allure-report",
            keep: Optional[int] = None,
    ) -> int:
        """
        修剪 history 目录，只保留最近 N 次构建

        :param report_dir: allure-report 目录
        :param keep:       保留最近 N 次（None 从配置读取）
        :return:           被删除的构建数
        """
        keep = keep or settings.get("history.keep", cls.DEFAULT_KEEP)
        history_dir = os.path.join(report_dir, "history")

        if not os.path.isdir(history_dir):
            logger.info(f"[History] 无 history 目录，跳过修剪: {history_dir}")
            return 0

        # 找到所有构建目录（Allure 的 history 目录包含多个文件，按时间戳排序）
        # history 文件命名：history-trend.json / history-duration-trend.json 等
        # 判断构建次数需要读 history-trend.json
        trend_file = os.path.join(history_dir, "history-trend.json")
        if not os.path.exists(trend_file):
            logger.debug("[History] 无 history-trend.json，跳过修剪")
            return 0

        try:
            # 读取 trend 历史
            trend_data = FileUtil.load_json(trend_file)
            if not isinstance(trend_data, list):
                return 0

            original_count = len(trend_data)
            if original_count <= keep:
                logger.debug(
                    f"[History] 历史数 {original_count} <= {keep}，无需修剪"
                )
                return 0

            # 保留最近 N 条
            trimmed = trend_data[-keep:]
            FileUtil.dump_json(trimmed, trend_file)

            # 删除对应的 report 目录（Allure 会保留以 buildOrder 命名的子目录）
            removed = cls._remove_old_reports(history_dir, trend_data[:original_count - keep])

            logger.info(
                f"[History] 修剪完成：{original_count} → {keep} "
                f"（删除 {removed} 个子目录）"
            )
            return original_count - keep

        except Exception as e:
            logger.warning(f"[History] 修剪失败: {e}")
            return 0

    # ============================================================
    # 3. 便捷方法：一键准备（生成前调用）
    # ============================================================
    @classmethod
    def setup_before_generate(
            cls,
            results_dir: str = "reports/allure-results",
            report_dir: str = "reports/allure-report",
    ) -> None:
        """
        生成 Allure 报告前的一键准备
        """
        cls.prepare_history(results_dir, report_dir)

    # ============================================================
    # 4. 便捷方法：生成后清理
    # ============================================================
    @classmethod
    def cleanup_after_generate(
            cls,
            report_dir: str = "reports/allure-report",
            keep: Optional[int] = None,
    ) -> None:
        """
        生成 Allure 报告后的一键清理
        """
        cls.prune_history(report_dir, keep)

    # ============================================================
    # 5. 内部辅助
    # ============================================================
    @staticmethod
    def _is_jenkins_plugin_managed() -> bool:
        """
        判断是否由 Jenkins Allure Plugin 管理历史

        检测逻辑：Jenkins 环境变量存在，且框架未显式要求"手动管理"
        """
        if not os.getenv("JENKINS_URL"):
            return False
        # 允许通过环境变量强制手动管理（如 Jenkins 使用了自定义脚本）
        if os.getenv("API_TEST_MANUAL_HISTORY") == "1":
            return False
        return True

    @staticmethod
    def _remove_old_reports(history_dir: str, old_builds: List[dict]) -> int:
        """
        删除旧构建对应的子目录

        Allure 的 history 目录结构：
            history/
            ├── history-trend.json
            ├── history-duration-trend.json
            ├── history-categories-trend.json
            ├── <timestamp>/
            │   ├── data/attachments/...
            │   └── ...
            └── <timestamp2>/

        删除策略：根据 history-trend.json 中的 buildOrder 推断目录名
        """
        if not old_builds:
            return 0

        removed = 0
        # 收集要删除的 buildOrder
        old_orders = {item.get("buildOrder") for item in old_builds if item.get("buildOrder")}
        if not old_orders:
            return 0

        for entry in os.listdir(history_dir):
            entry_path = os.path.join(history_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            try:
                # 目录名可能是时间戳数字
                if entry.isdigit() and int(entry) in old_orders:
                    shutil.rmtree(entry_path)
                    removed += 1
            except Exception as e:
                logger.debug(f"[History] 删除子目录失败 {entry}: {e}")

        return removed

    # ============================================================
    # 6. 诊断接口（供脚本调用）
    # ============================================================
    @classmethod
    def get_history_info(cls, report_dir: str = "reports/allure-report") -> dict:
        """
        获取历史信息（用于调试和统计）

        :return:
            {
                "exists": bool,
                "build_count": int,
                "oldest_build": str / None,
                "newest_build": str / None,
                "trend_file": str,
            }
        """
        history_dir = os.path.join(report_dir, "history")
        trend_file = os.path.join(history_dir, "history-trend.json")

        info = {
            "exists": os.path.isdir(history_dir),
            "build_count": 0,
            "oldest_build": None,
            "newest_build": None,
            "trend_file": trend_file,
        }

        if not os.path.exists(trend_file):
            return info

        try:
            trend_data = FileUtil.load_json(trend_file)
            if isinstance(trend_data, list):
                info["build_count"] = len(trend_data)
                if trend_data:
                    info["oldest_build"] = trend_data[0].get("buildOrder")
                    info["newest_build"] = trend_data[-1].get("buildOrder")
        except Exception as e:
            logger.warning(f"[History] 读取 trend 失败: {e}")

        return info