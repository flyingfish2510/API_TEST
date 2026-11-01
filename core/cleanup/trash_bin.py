# -*- coding: utf-8 -*-
"""
core/cleanup/trash_bin.py
-------------------------
全局数据回收站（TrashBin）

职责：
    1. 登记（register）测试过程中创建的各类资源
    2. 根据配置在测试会话结束时，统一逆序清理
    3. 单条清理失败不影响其他资源
    4. 生成清理结果报告

支持的资源清理方式：
    - 调用 API 删除（如 DELETE /char/user/deleteUser）
    - 执行 SQL 删除（如 DELETE FROM orders WHERE ...）

用法：
    from core.cleanup import TrashBin

    # 用例内：登记资源
    TrashBin.register("user", resource_id=user_id, context={"phone": phone})
    TrashBin.register("order", resource_id=order_no, context={"user_id": user_id})

    # 全局：会话结束统一清理（由 pytest_plugins 自动调用）
    TrashBin.cleanup_all()
"""
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

from config import settings
from common.utils import FileUtil

logger = logging.getLogger(__name__)


# ============================================================
# 资源类型枚举（与业务对齐）
# ============================================================
class ResourceType(str, Enum):
    """支持的资源类型"""
    USER = "user"  # 电商-用户
    ORDER = "order"  # 电商-订单
    CARRIER = "carrier"  # 物流-承运商
    PAYMENT = "payment"  # 电商-支付流水
    LOGISTICS_ORDER = "logistics_order"  # 物流-订单
    COST_BILL = "cost_bill"  # 物流-对账单
    CUSTOM = "custom"  # 自定义（使用显式清理方法）


# ============================================================
# 单个资源记录
# ============================================================
class TrashItem:
    """回收站中的单个资源记录"""

    def __init__(self, resource_type: str, resource_id: str, context: Optional[dict] = None):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.context = context or {}
        self.registered_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.resource_type,
            "id": self.resource_id,
            "context": self.context,
            "registered_at": self.registered_at,
        }

    def __repr__(self) -> str:
        return f"<TrashItem {self.resource_type}:{self.resource_id}>"


# ============================================================
# 清理策略配置（从 settings 读取）
# ============================================================
class CleanupStrategy:
    """清理策略（决定某个资源通过什么方式清理）"""

    # 默认清理接口映射（对应你的接口文档）
    DEFAULT_API_MAP = {
        ResourceType.USER: {
            "method": "POST",
            "url": "/char/user/deleteUser",
            "payload_tpl": {"user_id": "{id}"},
        },
        ResourceType.CARRIER: {
            "method": "POST",
            "url": "/api/user/pc/carrier/carrier/delete",
            "payload_tpl": {"carrierId": "{id}"},
        },
        # 订单、物流单通常没有删除接口，用 SQL 兜底
    }

    # 默认 SQL 清理映射
    DEFAULT_SQL_MAP = {
        ResourceType.ORDER: "DELETE FROM orders WHERE order_number = '{id}'",
        ResourceType.PAYMENT: "DELETE FROM payments WHERE order_number = '{id}'",
        ResourceType.LOGISTICS_ORDER: "DELETE FROM logistics_order WHERE order_no = '{id}'",
        ResourceType.COST_BILL: "DELETE FROM cost_bill WHERE bill_no = '{id}'",
    }


# ============================================================
# 全局回收站
# ============================================================
class TrashBin:
    """
    全局数据回收站（静态方法）
    """

    # 资源栈（LIFO，保证逆序清理）
    _items: List[TrashItem] = []

    # 是否启用（从 settings 读取，local/uat/test 通常为 True，prod 为 False）
    _enabled: Optional[bool] = None

    # 清理报告
    _report: Dict[str, Any] = {
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
    }

    # ============================================================
    # 1. 登记资源
    # ============================================================
    @classmethod
    def register(
            cls,
            resource_type: Any,
            resource_id: Any,
            context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        登记一个需要清理的资源

        :param resource_type: 资源类型（ResourceType 枚举或字符串）
        :param resource_id:   资源唯一标识（如 user_id, order_no）
        :param context:       附加信息（如创建时的手机号，便于精确清理）

        示例：
            TrashBin.register(ResourceType.USER, user_id, {"phone": phone})
        """
        if not cls._is_enabled():
            return

        if isinstance(resource_type, ResourceType):
            resource_type = resource_type.value

        item = TrashItem(str(resource_type), str(resource_id), context)
        cls._items.append(item)
        logger.debug(f"[TrashBin] 登记资源: {item}")

    # ============================================================
    # 2. 统一清理
    # ============================================================
    @classmethod
    def cleanup_all(cls, write_report: bool = True) -> Dict[str, Any]:
        """
        逆序清理所有已登记的资源

        :param write_report: 是否将清理报告写入 JSON 文件
        :return:             清理报告字典
        """
        if not cls._is_enabled():
            logger.info("[TrashBin] 当前环境未启用清理，跳过")
            return {"enabled": False}

        if not cls._items:
            logger.info("[TrashBin] 无需清理（回收站为空）")
            return {"enabled": True, "total": 0}

        cls._report = {
            "total": len(cls._items),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": [],
        }

        logger.info(f"[TrashBin] 开始逆序清理 {len(cls._items)} 个资源...")

        # 逆序清理（LIFO）
        for item in reversed(cls._items):
            cls._cleanup_one(item)

        # 生成报告
        cls._generate_report(write_report)

        # 清空回收站
        cls._items.clear()

        logger.info(
            f"[TrashBin] 清理完成: "
            f"成功 {cls._report['success']}, "
            f"失败 {cls._report['failed']}, "
            f"跳过 {cls._report['skipped']}"
        )
        return cls._report

    @classmethod
    def _cleanup_one(cls, item: TrashItem) -> None:
        """清理单个资源"""
        detail = {
            "type": item.resource_type,
            "id": item.resource_id,
            "status": "failed",
            "message": "",
            "strategy": None,
        }

        try:
            # 优先使用 API 清理
            api_cfg = CleanupStrategy.DEFAULT_API_MAP.get(item.resource_type)
            if api_cfg:
                detail["strategy"] = "api"
                cls._cleanup_via_api(item, api_cfg)
                detail["status"] = "success"
                cls._report["success"] += 1
                logger.debug(f"[TrashBin] ✅ API 清理成功: {item}")
                return

            # 其次使用 SQL 清理
            sql_tpl = CleanupStrategy.DEFAULT_SQL_MAP.get(item.resource_type)
            if sql_tpl:
                detail["strategy"] = "sql"
                cls._cleanup_via_sql(item, sql_tpl)
                detail["status"] = "success"
                cls._report["success"] += 1
                logger.debug(f"[TrashBin] ✅ SQL 清理成功: {item}")
                return

            # 无法识别的类型
            detail["status"] = "skipped"
            detail["message"] = f"未知资源类型或无清理策略: {item.resource_type}"
            cls._report["skipped"] += 1
            logger.warning(f"[TrashBin] ⚠️ 跳过: {item}")

        except Exception as e:
            detail["status"] = "failed"
            detail["message"] = str(e)
            cls._report["failed"] += 1
            logger.warning(f"[TrashBin] ❌ 清理失败: {item} - {e}")

        finally:
            cls._report["details"].append(detail)

    @classmethod
    def _cleanup_via_api(cls, item: TrashItem, api_cfg: dict) -> None:
        """通过 API 删除资源"""
        from common.client import HttpClient
        from common.client.auth import AuthManager
        from config import settings

        # 每次清理使用独立的 HttpClient，避免认证态被污染
        client = HttpClient(base_url=settings.get("api.base_url"))

        # 清理是系统级操作，使用 admin 账号执行
        auth = AuthManager(client, env=settings.env_name)
        auth.inject_headers(role="admin", project="ecommerce")

        # 渲染 payload 模板
        payload = {}
        for k, v in api_cfg["payload_tpl"].items():
            payload[k] = v.replace("{id}", item.resource_id)

        resp = client.request(
            method=api_cfg["method"],
            url=api_cfg["url"],
            json=payload if api_cfg["method"].upper() != "GET" else None,
            params=payload if api_cfg["method"].upper() == "GET" else None,
        )

        if resp.status_code >= 400:
            raise RuntimeError(f"API 返回异常状态码: {resp.status_code} - {resp.text[:200]}")

        # 电商删除接口返回 msg_code == 200 表示成功
        try:
            body = resp.json()
            if "msg_code" in body and body["msg_code"] != 200:
                raise RuntimeError(f"业务失败: {body.get('msg')}")
        except Exception:
            pass  # 非 JSON 响应不校验业务码

    @classmethod
    def _cleanup_via_sql(cls, item: TrashItem, sql_tpl: str) -> None:
        """通过 SQL 删除资源"""
        from core.assertions.db_assert import DBAssert

        sql = sql_tpl.replace("{id}", item.resource_id)
        affected = DBAssert.execute(sql)
        logger.debug(f"[TrashBin] SQL 影响行数: {affected}")

    # ============================================================
    # 3. 辅助方法
    # ============================================================
    @classmethod
    def clear(cls) -> None:
        """清空回收站但不执行清理（用于会话重置）"""
        cls._items.clear()
        logger.info("[TrashBin] 已清空（未执行清理）")

    @classmethod
    def size(cls) -> int:
        """当前回收站登记的资源数"""
        return len(cls._items)

    @classmethod
    def list_resources(cls) -> List[Dict[str, Any]]:
        """列出当前所有已登记的资源"""
        return [item.to_dict() for item in cls._items]

    @classmethod
    def _is_enabled(cls) -> bool:
        """是否启用清理（根据环境配置决定）"""
        if cls._enabled is None:
            cls._enabled = settings.get("cleanup.enabled", True)
        return cls._enabled

    @classmethod
    def _generate_report(cls, write_to_file: bool) -> None:
        """生成清理报告 JSON"""
        if not write_to_file:
            return

        report_path = settings.get(
            "cleanup.report_file", "reports/artifacts/cleanup-report.json"
        )
        report = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "env": settings.env_name,
            "summary": {
                "total": cls._report["total"],
                "success": cls._report["success"],
                "failed": cls._report["failed"],
                "skipped": cls._report["skipped"],
            },
            "details": cls._report["details"],
        }

        try:
            FileUtil.dump_json(report, report_path)
            logger.info(f"[TrashBin] 清理报告已写入: {report_path}")
        except Exception as e:
            logger.warning(f"[TrashBin] 清理报告写入失败: {e}")


# ============================================================
# 全局单例访问
# ============================================================
def get_trash_bin() -> type:
    """
    获取回收站（返回类本身，因为 TrashBin 使用类级别静态方法）

    用法：
        bin = get_trash_bin()
        bin.register("user", user_id=123)
    """
    return TrashBin