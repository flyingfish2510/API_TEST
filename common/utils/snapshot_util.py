# -*- coding: utf-8 -*-
"""
common/utils/snapshot_util.py
-----------------------------
基线快照工具类（SnapshotUtil）

职责：
    1. 将首次成功的响应结构保存为基线快照
    2. 后续执行时与基线快照对比
    3. 输出对比结果（新增/缺失/类型变更）
    4. 支持忽略不稳定字段（如 createTime / timestamp / request_id）

对比模式：
    - strict: 严格模式（缺失字段也视为失败）
    - loose:  宽松模式（只报告，不失败）
    - type_only: 只检查类型变化（字段值可变，但类型不能变）

依赖：
    pip install deepdiff
"""
import os
import json
import logging
import hashlib
from typing import Any, Dict, List, Optional, Set
from datetime import datetime

from common.utils.file_util import FileUtil

logger = logging.getLogger(__name__)


class SnapshotUtil:
    """
    快照工具类（全静态方法）
    """

    # ============================================================
    # 默认忽略字段（不稳定字段，不参与对比）
    # ============================================================
    DEFAULT_IGNORE_FIELDS: Set[str] = {
        "createTime",
        "updateTime",
        "queryTime",
        "server_time",
        "secache_date",
        "secache_time",
        "timestamp",
        "timeStamp",
        "request_id",
        "traceId",
        "trace_id",
    }

    # ============================================================
    # 1. 快照保存
    # ============================================================
    @staticmethod
    def save(
            data: Any,
            name: str,
            snapshot_dir: str = "data/snapshots",
            force: bool = False,
    ) -> bool:
        """
        保存快照

        :param data:         响应 JSON
        :param name:         快照名称（建议用 模块/接口名，如 ecommerce/order_flow）
        :param snapshot_dir: 快照保存目录
        :param force:        是否覆盖已有快照
        :return:             True 保存成功 / False 已存在未覆盖
        """
        file_path = SnapshotUtil._get_snapshot_path(name, snapshot_dir)

        if os.path.exists(file_path) and not force:
            logger.debug(f"[Snapshot] 已存在，跳过保存: {file_path}")
            return False

        # 清理不稳定字段
        cleaned = SnapshotUtil._clean_data(data)

        # 附加元数据
        snapshot = {
            "_meta": {
                "name": name,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "hash": SnapshotUtil._hash(cleaned),
            },
            "_data": cleaned,
        }

        FileUtil.dump_json(snapshot, file_path, indent=2)
        logger.info(f"[Snapshot] 保存快照: {file_path}")
        return True

    @staticmethod
    def load(name: str, snapshot_dir: str = "data/snapshots") -> Optional[Dict]:
        """
        加载快照

        :return: {"_meta": {...}, "_data": {...}} 或 None（不存在）
        """
        file_path = SnapshotUtil._get_snapshot_path(name, snapshot_dir)
        if not os.path.exists(file_path):
            logger.debug(f"[Snapshot] 快照不存在: {file_path}")
            return None
        return FileUtil.load_json(file_path)

    # ============================================================
    # 2. 与基线对比
    # ============================================================
    @staticmethod
    def compare(
            current: Any,
            name: str,
            snapshot_dir: str = "data/snapshots",
            mode: str = "loose",
            ignore_fields: Optional[Set[str]] = None,
            auto_save: bool = True,
    ) -> Dict[str, Any]:
        """
        将当前响应与基线快照对比

        :param current:       当前响应
        :param name:          快照名称
        :param snapshot_dir:  快照目录
        :param mode:          'strict' / 'loose' / 'type_only'
        :param ignore_fields: 额外忽略字段（与 DEFAULT_IGNORE_FIELDS 合并）
        :param auto_save:     基线不存在时自动保存
        :return:              对比结果字典：
            {
                "matched": bool,           # 是否通过
                "is_first_run": bool,      # 是否首次运行
                "added": [...],            # 新增字段路径
                "removed": [...],          # 缺失字段路径
                "type_changed": [...],     # 类型变更列表
                "value_changed": [...],    # 值变更（loose 模式忽略）
                "message": str,            # 人类可读摘要
            }
        """
        baseline = SnapshotUtil.load(name, snapshot_dir)

        # 首次运行：保存为基线
        if baseline is None:
            if auto_save:
                SnapshotUtil.save(current, name, snapshot_dir, force=False)
                logger.info(f"[Snapshot] 首次运行，已创建基线: {name}")
            return {
                "matched": True,
                "is_first_run": True,
                "added": [],
                "removed": [],
                "type_changed": [],
                "value_changed": [],
                "message": "首次运行，已创建基线快照",
            }

        baseline_data = baseline.get("_data", {})
        current_clean = SnapshotUtil._clean_data(current)

        # 合并忽略字段
        ignore_set = set(SnapshotUtil.DEFAULT_IGNORE_FIELDS)
        if ignore_fields:
            ignore_set.update(ignore_fields)

        # 执行对比
        try:
            from deepdiff import DeepDiff
        except ImportError:
            raise ImportError("快照对比需 deepdiff，请执行: pip install deepdiff")

        diff = DeepDiff(
            baseline_data,
            current_clean,
            ignore_order=True,
            exclude_paths=[
                f"root['{f}']" for f in ignore_set
            ],
            verbose_level=2,
        )

        # 解析 diff
        added = []
        removed = []
        type_changed = []
        value_changed = []

        # 新增字段
        for path in diff.get("dictionary_item_added", []):
            added.append(str(path))
        for path in diff.get("iterable_item_added", {}).keys():
            added.append(str(path))

        # 缺失字段
        for path in diff.get("dictionary_item_removed", []):
            removed.append(str(path))
        for path in diff.get("iterable_item_removed", {}).keys():
            removed.append(str(path))

        # 类型变更
        for path, info in diff.get("type_changes", {}).items():
            type_changed.append({
                "path": str(path),
                "old_type": info["old_type"].__name__,
                "new_type": info["new_type"].__name__,
                "old_value": str(info["old_value"])[:50],
                "new_value": str(info["new_value"])[:50],
            })

        # 值变更
        for path, info in diff.get("values_changed", {}).items():
            value_changed.append({
                "path": str(path),
                "old": str(info["old_value"])[:50],
                "new": str(info["new_value"])[:50],
            })

        # 判定是否通过
        matched = SnapshotUtil._judge(mode, added, removed, type_changed, value_changed)

        result = {
            "matched": matched,
            "is_first_run": False,
            "added": added,
            "removed": removed,
            "type_changed": type_changed,
            "value_changed": value_changed,
            "message": SnapshotUtil._build_message(
                mode, matched, added, removed, type_changed, value_changed
            ),
        }

        if matched:
            logger.info(f"[Snapshot] 对比通过: {name}")
        else:
            logger.warning(f"[Snapshot] 对比失败: {name} - {result['message']}")

        return result

    # ============================================================
    # 3. 断言入口（供 YAML 引擎调用）
    # ============================================================
    @staticmethod
    def assert_compare(
            current: Any,
            name: str,
            snapshot_dir: str = "data/snapshots",
            mode: str = "loose",
            ignore_fields: Optional[Set[str]] = None,
    ) -> None:
        """
        执行快照对比并在失败时抛异常
        供 YAML 的 validate 节点使用：
            - snapshot: {name: "order_flow", mode: "strict"}
        """
        result = SnapshotUtil.compare(
            current, name, snapshot_dir, mode, ignore_fields
        )
        if not result["matched"]:
            raise AssertionError(
                f"快照对比失败 [{name}]: {result['message']}"
            )

    # ============================================================
    # 4. 基线管理
    # ============================================================
    @staticmethod
    def update(
            current: Any,
            name: str,
            snapshot_dir: str = "data/snapshots",
    ) -> None:
        """手动更新基线（用于"接口变更后确认新结构"）"""
        SnapshotUtil.save(current, name, snapshot_dir, force=True)
        logger.info(f"[Snapshot] 已更新基线: {name}")

    @staticmethod
    def remove(name: str, snapshot_dir: str = "data/snapshots") -> bool:
        """删除单个基线"""
        file_path = SnapshotUtil._get_snapshot_path(name, snapshot_dir)
        return FileUtil.remove(file_path, ignore_missing=True)

    @staticmethod
    def list_all(snapshot_dir: str = "data/snapshots") -> List[str]:
        """列出所有基线名称"""
        if not os.path.isdir(snapshot_dir):
            return []
        names = []
        for f in FileUtil.list_files(snapshot_dir, suffix=".json"):
            rel = os.path.relpath(f, snapshot_dir)
            names.append(rel.replace(".json", "").replace("\\", "/"))
        return sorted(names)

    # ============================================================
    # 5. 内部辅助
    # ============================================================
    @staticmethod
    def _get_snapshot_path(name: str, snapshot_dir: str) -> str:
        """构造快照文件路径（name 中可含子目录，如 ecommerce/order_flow）"""
        # 确保 name 合法（不能有 .. 逃逸）
        safe_name = name.replace("..", "").strip("/").replace("\\", "/")
        return os.path.join(snapshot_dir, f"{safe_name}.json")

    @staticmethod
    def _clean_data(data: Any, ignore_fields: Optional[Set[str]] = None) -> Any:
        """
        清理数据：移除不稳定字段
        - 递归处理 dict
        - 遍历 list
        - 遇到 ignore_fields 中的键时跳过
        """
        if ignore_fields is None:
            ignore_fields = SnapshotUtil.DEFAULT_IGNORE_FIELDS

        if isinstance(data, dict):
            return {
                k: SnapshotUtil._clean_data(v, ignore_fields)
                for k, v in data.items()
                if k not in ignore_fields
            }
        if isinstance(data, list):
            return [SnapshotUtil._clean_data(item, ignore_fields) for item in data]
        return data

    @staticmethod
    def _hash(data: Any) -> str:
        """对数据做内容哈希，便于快速判断是否相同"""
        s = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _judge(
            mode: str,
            added: List,
            removed: List,
            type_changed: List,
            value_changed: List,
    ) -> bool:
        """
        根据对比模式判定是否通过

        - loose:      新增/值变更为警告，缺失/类型变更为失败
        - strict:     任何差异都视为失败
        - type_only:  只有类型变更才失败（值可变、字段可增删）
        """
        mode = mode.lower()

        if mode == "strict":
            return not (added or removed or type_changed or value_changed)

        if mode == "loose":
            # 缺失字段或类型变更 → 失败；新增/值变更 → 允许
            return not (removed or type_changed)

        if mode == "type_only":
            # 只有类型变更视为失败
            return not type_changed

        raise ValueError(f"不支持的快照对比模式: {mode}")

    @staticmethod
    def _build_message(
            mode: str,
            matched: bool,
            added: List,
            removed: List,
            type_changed: List,
            value_changed: List,
    ) -> str:
        """构建人类可读摘要"""
        if matched:
            return "快照对比通过"

        parts = [f"模式={mode}"]
        if added:
            parts.append(f"新增字段 {len(added)} 个")
        if removed:
            parts.append(f"缺失字段 {len(removed)} 个: {removed[:3]}")
        if type_changed:
            items = [
                f"{t['path']}: {t['old_type']}→{t['new_type']}"
                for t in type_changed[:3]
            ]
            parts.append(f"类型变更: {items}")
        if value_changed:
            parts.append(f"值变更 {len(value_changed)} 处")

        return " | ".join(parts)

    # ============================================================
    # 6. 通用入口（供 VariableResolver 动态调用）
    # ============================================================
    @classmethod
    def call(cls, method: str, *args, **kwargs):
        """
        动态调用入口：
            {{snapshot.compare(last_json, 'ecommerce/order_flow')}}
        """
        if not hasattr(cls, method):
            raise AttributeError(f"SnapshotUtil 无此方法: {method}")
        return getattr(cls, method)(*args, **kwargs)