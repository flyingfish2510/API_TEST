# -*- coding: utf-8 -*-
"""
scripts/clean_reports.py
------------------------
测试报告与历史数据清理脚本

功能：
    1. 清理 reports/ 下的旧构建产物（按保留次数或天数）
    2. 修剪 Allure history 目录（保留最近 N 次）
    3. 清理孤立附件（无对应测试结果的附件）
    4. 清理临时文件、空目录
    5. 支持 dry-run 模式（只展示不删除）

用法：
    # 清理：保留最近 20 次构建
    python scripts/clean_reports.py --keep=20

    # 清理：保留最近 7 天
    python scripts/clean_reports.py --days=7

    # 预览：不实际删除
    python scripts/clean_reports.py --keep=20 --dry-run

    # 清理所有（谨慎）
    python scripts/clean_reports.py --all --confirm
"""
import os
import sys
import shutil
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clean_reports")


# ============================================================
# 清理目标定义
# ============================================================
class CleanTarget:
    """清理目标目录定义"""

    # 默认需要清理的目标
    REPORTS_DIR = PROJECT_ROOT / "reports"
    ALLURE_RESULTS = PROJECT_ROOT / "reports" / "allure-results"
    ALLURE_REPORT = PROJECT_ROOT / "reports" / "allure-report"
    ALLURE_HISTORY = PROJECT_ROOT / "reports" / "allure-report" / "history"
    JUNIT_DIR = PROJECT_ROOT / "reports" / "junit"
    LOGS_DIR = PROJECT_ROOT / "reports" / "logs"
    ARTIFACTS_DIR = PROJECT_ROOT / "reports" / "artifacts"

    # 需要保留的占位文件（不删除）
    KEEP_FILES = {".gitkeep", ".gitignore"}

    # 需要保留的目录（清理时跳过）
    KEEP_DIRS = {"history"}  # history 单独处理


# ============================================================
# 清理器
# ============================================================
class ReportCleaner:
    """报告清理器"""

    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self.dry_run = dry_run
        self.verbose = verbose
        self.stats = {
            "deleted_files": 0,
            "deleted_dirs": 0,
            "freed_bytes": 0,
            "skipped": 0,
            "errors": 0,
        }

    # ============================================================
    # 主入口
    # ============================================================
    def clean(
            self,
            keep_count: int = None,
            keep_days: int = None,
            clean_all: bool = False,
    ) -> dict:
        """
        执行清理

        :param keep_count: 保留最近 N 次构建（按修改时间排序）
        :param keep_days:  保留最近 N 天
        :param clean_all:  清理全部
        :return:           统计字典
        """
        mode = "dry-run" if self.dry_run else "实际删除"
        logger.info(f"===== 报告清理开始（模式: {mode}）=====")

        if clean_all:
            logger.info("🧹 模式：清理全部")
            self._clean_all_targets()
        elif keep_days is not None:
            logger.info(f"🧹 模式：保留最近 {keep_days} 天")
            cutoff = datetime.now() - timedelta(days=keep_days)
            self._clean_by_time(cutoff)
        elif keep_count is not None:
            logger.info(f"🧹 模式：保留最近 {keep_count} 次构建")
            self._clean_by_count(keep_count)
        else:
            logger.error("必须指定 --keep / --days / --all 之一")
            return self.stats

        # 清理空目录
        self._clean_empty_dirs(CleanTarget.REPORTS_DIR)

        # 输出统计
        self._print_stats()
        return self.stats

    # ============================================================
    # 按构建次数清理
    # ============================================================
    def _clean_by_count(self, keep_count: int):
        """保留最近 N 次构建"""
        # 1. 清理 allure-results 中旧结果
        self._clean_dir_by_count(CleanTarget.ALLURE_RESULTS, keep_count)

        # 2. 清理 junit XML 文件
        self._clean_dir_by_count(CleanTarget.JUNIT_DIR, keep_count, pattern="*.xml")

        # 3. 清理 logs 日志文件
        self._clean_dir_by_count(CleanTarget.LOGS_DIR, keep_count, pattern="*.log*")

        # 4. 清理 artifacts 中的旧报告
        self._clean_dir_by_count(CleanTarget.ARTIFACTS_DIR, keep_count, pattern="*.json")

        # 5. 清理 allure 报告（保留最近 N 个目录）
        self._clean_dir_by_count(CleanTarget.ALLURE_REPORT, keep_count)

        # 6. 修剪 history
        self._prune_history(keep_count)

    # ============================================================
    # 按时间清理
    # ============================================================
    def _clean_by_time(self, cutoff: datetime):
        """清理早于 cutoff 的文件"""
        # 1. 清理 allure-results
        self._clean_dir_by_time(CleanTarget.ALLURE_RESULTS, cutoff)

        # 2. junit
        self._clean_dir_by_time(CleanTarget.JUNIT_DIR, cutoff)

        # 3. logs
        self._clean_dir_by_time(CleanTarget.LOGS_DIR, cutoff)

        # 4. artifacts
        self._clean_dir_by_time(CleanTarget.ARTIFACTS_DIR, cutoff)

        # 5. history
        self._prune_history_by_time(cutoff)

    # ============================================================
    # 清理全部
    # ============================================================
    def _clean_all_targets(self):
        """清理全部内容（保留 .gitkeep）"""
        targets = [
            CleanTarget.ALLURE_RESULTS,
            CleanTarget.ALLURE_REPORT,
            CleanTarget.JUNIT_DIR,
            CleanTarget.LOGS_DIR,
            CleanTarget.ARTIFACTS_DIR,
        ]
        for target in targets:
            self._clean_dir_all(target)

    # ============================================================
    # 底层清理方法
    # ============================================================
    def _clean_dir_by_count(self, target_dir: Path, keep: int, pattern: str = "*"):
        """按次数清理目录（保留最新的 N 个）"""
        if not target_dir.exists():
            logger.debug(f"目录不存在: {target_dir}")
            return

        items = self._list_items(target_dir, pattern)
        if len(items) <= keep:
            logger.info(f"  {target_dir.name}: {len(items)} 项 <= {keep}，无需清理")
            return

        # 按修改时间倒序（最新的在前）
        items.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        to_delete = items[keep:]
        logger.info(f"  {target_dir.name}: 清理 {len(to_delete)} / {len(items)} 项")

        for item in to_delete:
            self._safe_delete(item)

    def _clean_dir_by_time(self, target_dir: Path, cutoff: datetime):
        """按时间清理目录"""
        if not target_dir.exists():
            return

        items = self._list_items(target_dir, "*")
        to_delete = [i for i in items if i.stat().st_mtime < cutoff.timestamp()]

        if not to_delete:
            logger.info(f"  {target_dir.name}: 无需清理")
            return

        logger.info(f"  {target_dir.name}: 清理 {len(to_delete)} / {len(items)} 项")
        for item in to_delete:
            self._safe_delete(item)

    def _clean_dir_all(self, target_dir: Path):
        """清理目录下全部内容"""
        if not target_dir.exists():
            return

        items = self._list_items(target_dir, "*")
        logger.info(f"  {target_dir.name}: 清理全部 {len(items)} 项")
        for item in items:
            self._safe_delete(item)

    def _prune_history(self, keep: int):
        """修剪 history 目录"""
        history_dir = CleanTarget.ALLURE_HISTORY
        if not history_dir.exists():
            logger.debug(f"history 目录不存在: {history_dir}")
            return

        # history 目录的特殊性：保留 *.json + 最近 N 个 buildOrder 子目录
        json_files = sorted(
            [f for f in history_dir.glob("*.json")],
            key=lambda x: x.stat().st_mtime,
        )

        # 读取 history-trend.json 判断构建数
        trend_file = history_dir / "history-trend.json"
        if not trend_file.exists():
            logger.debug("history-trend.json 不存在，跳过修剪")
            return

        try:
            import json
            with open(trend_file, "r", encoding="utf-8") as f:
                trend = json.load(f)

            if len(trend) <= keep:
                logger.info(f"  history: {len(trend)} 次 <= {keep}，无需修剪")
                return

            # 保留最近 N 条
            trimmed = trend[-keep:]
            if not self.dry_run:
                with open(trend_file, "w", encoding="utf-8") as f:
                    json.dump(trimmed, f, indent=2, ensure_ascii=False)

            logger.info(f"  history: 修剪 {len(trend) - keep} 次（保留 {keep} 次）")

            # 删除对应的子目录
            old_orders = {item.get("buildOrder") for item in trend[:len(trend) - keep]}
            for subdir in history_dir.iterdir():
                if subdir.is_dir() and subdir.name.isdigit() and int(subdir.name) in old_orders:
                    self._safe_delete(subdir)

        except Exception as e:
            logger.error(f"  history 修剪失败: {e}")

    def _prune_history_by_time(self, cutoff: datetime):
        """按时间修剪 history"""
        history_dir = CleanTarget.ALLURE_HISTORY
        if not history_dir.exists():
            return

        # 删除早于 cutoff 的子目录
        for subdir in history_dir.iterdir():
            if subdir.is_dir() and subdir.name.isdigit():
                if subdir.stat().st_mtime < cutoff.timestamp():
                    self._safe_delete(subdir)

    # ============================================================
    # 辅助方法
    # ============================================================
    def _list_items(self, target_dir: Path, pattern: str) -> List[Path]:
        """列出目录下需要清理的项（排除保留文件/目录）"""
        items = []
        for item in target_dir.glob(pattern):
            # 跳过保留文件
            if item.name in CleanTarget.KEEP_FILES:
                continue
            # 跳过保留目录
            if item.is_dir() and item.name in CleanTarget.KEEP_DIRS:
                continue
            items.append(item)
        return items

    def _safe_delete(self, path: Path):
        """安全删除（支持 dry-run）"""
        try:
            size = self._get_size(path)

            if self.dry_run:
                if self.verbose:
                    logger.info(f"    [DRY-RUN] 将删除: {path.name} ({self._human_size(size)})")
            else:
                if path.is_dir():
                    shutil.rmtree(path)
                    self.stats["deleted_dirs"] += 1
                else:
                    path.unlink()
                    self.stats["deleted_files"] += 1

                if self.verbose:
                    logger.info(f"    ✓ 已删除: {path.name} ({self._human_size(size)})")

            self.stats["freed_bytes"] += size

        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"    删除失败: {path} - {e}")

    def _clean_empty_dirs(self, root: Path):
        """清理空目录（保留带 .gitkeep 的目录）"""
        if not root.exists():
            return

        for dirpath in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                if self.dry_run:
                    logger.debug(f"    [DRY-RUN] 将删除空目录: {dirpath.name}")
                else:
                    try:
                        dirpath.rmdir()
                        self.stats["deleted_dirs"] += 1
                    except OSError:
                        pass

    @staticmethod
    def _get_size(path: Path) -> int:
        """获取文件或目录大小"""
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
        return total

    @staticmethod
    def _human_size(size: int) -> str:
        """人类可读大小"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    def _print_stats(self):
        """输出统计"""
        s = self.stats
        freed = self._human_size(s["freed_bytes"])

        action = "将释放" if self.dry_run else "已释放"
        logger.info("=" * 50)
        logger.info(f"清理统计:")
        logger.info(f"  删除文件: {s['deleted_files']}")
        logger.info(f"  删除目录: {s['deleted_dirs']}")
        logger.info(f"  {action}: {freed}")
        logger.info(f"  错误: {s['errors']}")
        logger.info("=" * 50)


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="清理 API_TEST 测试报告与历史数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 保留最近 20 次构建
    python scripts/clean_reports.py --keep=20

    # 保留最近 7 天
    python scripts/clean_reports.py --days=7

    # 预览清理结果（不实际删除）
    python scripts/clean_reports.py --keep=20 --dry-run

    # 清理全部（需要二次确认）
    python scripts/clean_reports.py --all --confirm

    # 详细输出
    python scripts/clean_reports.py --keep=10 -v
        """,
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--keep", type=int, metavar="N",
                            help="保留最近 N 次构建")
    mode_group.add_argument("--days", type=int, metavar="D",
                            help="保留最近 D 天")
    mode_group.add_argument("--all", action="store_true",
                            help="清理全部（需要 --confirm）")

    parser.add_argument("--confirm", action="store_true",
                        help="确认执行危险操作（--all 时必须）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只展示不删除")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细输出")
    parser.add_argument("--reports-dir", type=str,
                        help="自定义报告目录（默认 reports/）")

    args = parser.parse_args()

    # 校验
    if args.keep is not None and args.keep < 1:
        parser.error("--keep 必须 >= 1")
    if args.days is not None and args.days < 1:
        parser.error("--days 必须 >= 1")
    if args.all and not args.confirm:
        parser.error("--all 需要配合 --confirm 使用（防止误操作）")

    # 自定义报告目录
    if args.reports_dir:
        CleanTarget.REPORTS_DIR = Path(args.reports_dir).resolve()
        CleanTarget.ALLURE_RESULTS = CleanTarget.REPORTS_DIR / "allure-results"
        CleanTarget.ALLURE_REPORT = CleanTarget.REPORTS_DIR / "allure-report"
        CleanTarget.ALLURE_HISTORY = CleanTarget.ALLURE_REPORT / "history"
        CleanTarget.JUNIT_DIR = CleanTarget.REPORTS_DIR / "junit"
        CleanTarget.LOGS_DIR = CleanTarget.REPORTS_DIR / "logs"
        CleanTarget.ARTIFACTS_DIR = CleanTarget.REPORTS_DIR / "artifacts"

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 执行
    cleaner = ReportCleaner(dry_run=args.dry_run, verbose=args.verbose)
    stats = cleaner.clean(
        keep_count=args.keep,
        keep_days=args.days,
        clean_all=args.all,
    )

    # 退出码
    if stats["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()