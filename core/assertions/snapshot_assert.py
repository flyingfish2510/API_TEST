# -*- coding: utf-8 -*-
"""
core/assertions/snapshot_assert.py
----------------------------------
基线快照断言器（SnapshotAssert）

职责：
    1. 调用 SnapshotUtil 完成快照对比
    2. 支持三种模式：strict / loose / type_only
    3. 支持灵活指定快照名、目录、忽略字段
    4. 失败抛 SnapshotMismatchError（业务失败，退出码 1）
    5. 详细输出新增/缺失/类型变更/值变更的字段路径

依赖：
    - common/utils/snapshot_util.py（快照读写与对比核心）
    - common/exceptions.py（SnapshotMismatchError）
"""
import logging
from typing import Any, Dict, List, Optional

from common.utils import SnapshotUtil
# ★ 关键：使用 SnapshotMismatchError（继承 ApiAssertionError）
from common.exceptions import SnapshotMismatchError, API_TESTException

logger = logging.getLogger(__name__)


class SnapshotAssert:
    """
    基线快照断言器（静态方法）
    """

    # 默认快照目录
    DEFAULT_SNAPSHOT_DIR = "data/snapshots"

    # 支持的对比模式
    SUPPORTED_MODES = ("strict", "loose", "type_only")

    # ============================================================
    # 核心断言入口
    # ============================================================
    @classmethod
    def assert_snapshot(
        cls,
        resp_json: Any,
        config: Any,
    ) -> None:
        """
        断言响应与基线快照一致

        :param resp_json: 响应 JSON
        :param config:    配置（支持两种写法）：
            - 字符串: "ecommerce/order_flow"（等价于 {"name": "...", "mode": "loose"}）
            - 字典:   {
                "name": "ecommerce/order_flow",     # 必填，快照名
                "mode": "loose",                    # 可选，默认 loose
                "snapshot_dir": "data/snapshots",   # 可选
                "ignore_fields": ["timestamp"],     # 可选，额外忽略字段
                "auto_save": true,                  # 可选，首次自动保存
              }

        :raises SnapshotMismatchError: 对比失败
        """
        # 兼容两种写法
        if isinstance(config, str):
            config = {"name": config}

        if not isinstance(config, dict):
            raise SnapshotMismatchError(
                f"snapshot 断言参数格式错误: {config}（应为 str 或 dict）",
                diff={"reason": "invalid_config"},
            )

        name = config.get("name")
        if not name:
            raise SnapshotMismatchError(
                "snapshot 断言缺少必填参数: name",
                diff={"reason": "missing_name"},
            )

        mode = config.get("mode", "loose").lower()
        if mode not in cls.SUPPORTED_MODES:
            raise SnapshotMismatchError(
                f"不支持的快照对比模式: {mode}，"
                f"可选: {cls.SUPPORTED_MODES}",
                diff={"reason": "invalid_mode", "mode": mode},
            )

        snapshot_dir = config.get("snapshot_dir", cls.DEFAULT_SNAPSHOT_DIR)
        ignore_fields = config.get("ignore_fields", [])
        auto_save = config.get("auto_save", True)

        if resp_json is None:
            raise SnapshotMismatchError(
                f"响应 JSON 为空，无法进行快照对比 [{name}]",
                diff={"reason": "empty_response"},
            )

        # 调用底层工具
        try:
            result = SnapshotUtil.compare(
                current=resp_json,
                name=name,
                snapshot_dir=snapshot_dir,
                mode=mode,
                ignore_fields=set(ignore_fields) if ignore_fields else None,
                auto_save=auto_save,
            )
        except API_TESTException:
            # 框架层异常直接抛
            raise
        except Exception as e:
            raise SnapshotMismatchError(
                f"快照对比执行异常 [{name}]: {e}",
                diff={"reason": "compare_error", "error": str(e)},
            ) from e

        # 首次运行特殊处理
        if result.get("is_first_run"):
            logger.info(
                f"[SnapshotAssert] 首次运行，已创建基线: {name} "
                f"(mode={mode})"
            )
            return

        # 对比结果判定
        if not result.get("matched"):
            cls._raise_mismatch(name, mode, result)

        logger.info(
            f"[SnapshotAssert] ✅ 快照对比通过: {name} (mode={mode})"
        )

    # ============================================================
    # 失败时构造详细异常
    # ============================================================
    @staticmethod
    def _raise_mismatch(name: str, mode: str, result: Dict[str, Any]) -> None:
        """构建并抛出 SnapshotMismatchError"""
        added = result.get("added", [])
        removed = result.get("removed", [])
        type_changed = result.get("type_changed", [])
        value_changed = result.get("value_changed", [])

        # 构建详细摘要
        summary_lines = [
            f"快照对比失败 [{name}] (mode={mode})",
        ]

        if removed:
            summary_lines.append(f"\n❌ 缺失字段 ({len(removed)} 个):")
            for path in removed[:10]:
                summary_lines.append(f"  - {path}")
            if len(removed) > 10:
                summary_lines.append(f"  ... 还有 {len(removed) - 10} 个")

        if type_changed:
            summary_lines.append(f"\n⚠️ 类型变更 ({len(type_changed)} 处):")
            for item in type_changed[:10]:
                summary_lines.append(
                    f"  - {item['path']}: "
                    f"{item['old_type']} → {item['new_type']}"
                )

        if added:
            summary_lines.append(f"\n🆕 新增字段 ({len(added)} 个):")
            for path in added[:5]:
                summary_lines.append(f"  - {path}")
            if len(added) > 5:
                summary_lines.append(f"  ... 还有 {len(added) - 5} 个")

        if value_changed and mode == "strict":
            summary_lines.append(f"\n🔄 值变更 ({len(value_changed)} 处):")
            for item in value_changed[:5]:
                summary_lines.append(
                    f"  - {item['path']}: {item['old']} → {item['new']}"
                )

        raise SnapshotMismatchError(
            message="\n".join(summary_lines),
            diff={
                "name": name,
                "mode": mode,
                "added": added,
                "removed": removed,
                "type_changed": type_changed,
                "value_changed": value_changed if mode == "strict" else [],
                "summary": result.get("message", ""),
            },
        )

    # ============================================================
    # 便捷方法：手动更新基线
    # ============================================================
    @classmethod
    def update_snapshot(
        cls,
        resp_json: Any,
        name: str,
        snapshot_dir: Optional[str] = None,
    ) -> None:
        """
        手动更新基线快照

        用途：
            接口有意变更后，确认新结构，更新基线
        """
        snapshot_dir = snapshot_dir or cls.DEFAULT_SNAPSHOT_DIR
        SnapshotUtil.update(resp_json, name, snapshot_dir)
        logger.info(f"[SnapshotAssert] 基线已更新: {name}")

    # ============================================================
    # 便捷方法：列出所有基线
    # ============================================================
    @classmethod
    def list_snapshots(
        cls,
        snapshot_dir: Optional[str] = None,
    ) -> List[str]:
        """列出所有已有的快照名"""
        snapshot_dir = snapshot_dir or cls.DEFAULT_SNAPSHOT_DIR
        return SnapshotUtil.list_all(snapshot_dir)

    # ============================================================
    # 便捷方法：删除基线
    # ============================================================
    @classmethod
    def remove_snapshot(
        cls,
        name: str,
        snapshot_dir: Optional[str] = None,
    ) -> bool:
        """删除指定基线"""
        snapshot_dir = snapshot_dir or cls.DEFAULT_SNAPSHOT_DIR
        return SnapshotUtil.remove(name, snapshot_dir)