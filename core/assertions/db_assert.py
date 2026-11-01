# -*- coding: utf-8 -*-
"""
core/assertions/db_assert.py
----------------------------
数据库直连断言器（DBAssert）

支持的断言：
    - assert_db_eq:     查询结果 == 期望值（首行首列）
    - assert_db_exists: 查询结果存在（非空）
    - assert_db_count:  查询结果行数 == 期望值
    - assert_db_row:    校验某行记录的多个字段

依赖：
    - common/db/db_client.py（DBClient 连接管理）
    - config/settings.py（读取当前环境数据库配置）

安全注意：
    - 生产环境（prod）默认禁止使用 db_assert（yaml 中 database.enabled=false）
    - 所有 SQL 必须有 WHERE 条件（禁止无限制全表操作）
"""
import logging
import re
from typing import Any, Dict, List, Optional

from common.db import DBClient
# ★ 关键：用 ApiAssertionError 别名，避免与内置 AssertionError 冲突
from common.exceptions import ApiAssertionError as AssertionError, DatabaseError
from config import settings

logger = logging.getLogger(__name__)


# ============================================================
# 危险 SQL 检测（禁止在生产执行）
# ============================================================
_DANGEROUS_SQL_PATTERN = re.compile(
    r"\b(DROP|TRUNCATE|ALTER|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class DBAssert:
    """
    数据库断言器（静态方法）

    内部通过 DBClient 单例复用连接，避免每次断言都新建连接。
    """

    # 共享的连接客户端（懒加载）
    _client: Optional[DBClient] = None

    # ============================================================
    # 客户端管理
    # ============================================================
    @classmethod
    def _get_client(cls) -> DBClient:
        """获取（或初始化）数据库客户端"""
        if not settings.get("database.enabled", False):
            raise DatabaseError(
                f"当前环境 {settings.env_name} 未启用数据库（database.enabled=false），"
                f"无法执行 db_assert"
            )

        if cls._client is None:
            cls._client = DBClient(
                host=settings.get("database.host"),
                port=settings.get("database.port", 3306),
                user=settings.get("database.user"),
                password=settings.get("database.password"),
                database=settings.get("database.db_name"),
                charset=settings.get("database.charset", "utf8mb4"),
                pool_size=settings.get("database.pool_size", 5),
            )
            logger.info(
                f"[DBAssert] 数据库客户端初始化: "
                f"{settings.get('database.host')}:{settings.get('database.port')}"
            )
        return cls._client

    @classmethod
    def close(cls) -> None:
        """关闭数据库连接（在 pytest session finish 时调用）"""
        if cls._client is not None:
            cls._client.close()
            cls._client = None
            logger.info("[DBAssert] 数据库连接已关闭")

    # ============================================================
    # SQL 安全检查
    # ============================================================
    @staticmethod
    def _check_sql_safety(sql: str) -> None:
        """禁止危险 SQL 在生产执行"""
        if _DANGEROUS_SQL_PATTERN.search(sql):
            raise DatabaseError(
                f"检测到危险 SQL（DROP/TRUNCATE/ALTER/GRANT/REVOKE），已阻止执行: {sql[:100]}"
            )

        # 生产环境禁止无 WHERE 的 UPDATE/DELETE
        if settings.env_name == "prod":
            upper = sql.upper().strip()
            if (upper.startswith("UPDATE") or upper.startswith("DELETE")) \
                    and " WHERE " not in upper:
                raise DatabaseError(
                    f"生产环境禁止无 WHERE 条件的 UPDATE/DELETE: {sql[:100]}"
                )

    # ============================================================
    # 1. 相等断言：首行首列 == 期望值
    # ============================================================
    @staticmethod
    def assert_db_eq(
        sql: str,
        expected: Any,
        *,
        params: Optional[tuple] = None,
        timeout: int = 5,
    ) -> None:
        """
        断言 SQL 查询结果的首行首列 == 期望值

        :param sql:      SELECT 语句（建议有 WHERE 条件）
        :param expected: 期望值
        :param params:   SQL 参数（防注入，支持 WHERE col = %s）
        :param timeout:  查询超时（秒）

        示例：
            DBAssert.assert_db_eq(
                "SELECT status FROM orders WHERE order_number = %s",
                "PAID",
                params=("DD202601050001",),
            )
        """
        DBAssert._check_sql_safety(sql)
        client = DBAssert._get_client()

        try:
            rows = client.query(sql, params=params, timeout=timeout)
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {sql[:100]} - {e}") from e

        if not rows:
            raise AssertionError(
                f"数据库查询无结果: {sql[:100]}",
                detail={
                    "sql": sql,
                    "params": params,
                    "expected": expected,
                    "reason": "no_rows",
                },
            )

        actual = rows[0][0] if isinstance(rows[0], (tuple, list)) else rows[0]

        # 类型转换：DB 返回的可能是 Decimal/date 等
        actual_cmp = _normalize_db_value(actual)
        expected_cmp = _normalize_db_value(expected)

        if actual_cmp != expected_cmp:
            raise AssertionError(
                f"数据库断言失败\n"
                f"  SQL: {sql[:200]}\n"
                f"  期望: {expected}\n"
                f"  实际: {actual}",
                detail={
                    "sql": sql,
                    "params": params,
                    "expected": expected,
                    "actual": actual,
                },
            )

        logger.info(f"[DBAssert] ✅ db_eq 通过: {sql[:80]}")

    # ============================================================
    # 2. 存在性断言
    # ============================================================
    @staticmethod
    def assert_db_exists(
        sql: str,
        *,
        params: Optional[tuple] = None,
        timeout: int = 5,
    ) -> None:
        """
        断言 SQL 查询结果存在（至少 1 行）

        示例：
            DBAssert.assert_db_exists(
                "SELECT 1 FROM users WHERE phone = %s",
                params=("13812345678",),
            )
        """
        DBAssert._check_sql_safety(sql)
        client = DBAssert._get_client()

        try:
            rows = client.query(sql, params=params, timeout=timeout)
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {sql[:100]} - {e}") from e

        if not rows:
            raise AssertionError(
                f"数据库查询无结果（期望存在）: {sql[:200]}",
                detail={
                    "sql": sql,
                    "params": params,
                    "reason": "no_rows",
                },
            )

        logger.info(f"[DBAssert] ✅ db_exists 通过: {sql[:80]}")

    # ============================================================
    # 3. 行数断言
    # ============================================================
    @staticmethod
    def assert_db_count(
        sql: str,
        expected_count: int,
        *,
        params: Optional[tuple] = None,
        timeout: int = 5,
    ) -> None:
        """
        断言 SQL 查询结果的行数 == 期望值

        示例：
            DBAssert.assert_db_count(
                "SELECT * FROM users WHERE batch_id = %s",
                3,
                params=("BATCH001",),
            )
        """
        DBAssert._check_sql_safety(sql)
        client = DBAssert._get_client()

        try:
            rows = client.query(sql, params=params, timeout=timeout)
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {sql[:100]} - {e}") from e

        actual_count = len(rows) if rows else 0

        if actual_count != expected_count:
            raise AssertionError(
                f"数据库行数不符\n"
                f"  SQL: {sql[:200]}\n"
                f"  期望: {expected_count} 行\n"
                f"  实际: {actual_count} 行",
                detail={
                    "sql": sql,
                    "params": params,
                    "expected_count": expected_count,
                    "actual_count": actual_count,
                },
            )

        logger.info(f"[DBAssert] ✅ db_count 通过: {actual_count} 行")

    # ============================================================
    # 4. 行级字段断言
    # ============================================================
    @staticmethod
    def assert_db_row(
        sql: str,
        expected_dict: Dict[str, Any],
        *,
        params: Optional[tuple] = None,
        timeout: int = 5,
    ) -> None:
        """
        断言某行记录的多个字段

        :param sql:           查询语句
        :param expected_dict: {字段名: 期望值}

        示例：
            DBAssert.assert_db_row(
                "SELECT status, amount, user_id FROM orders WHERE order_number = %s",
                {"status": "PAID", "amount": 199.80, "user_id": 1001},
                params=("DD202601050001",),
            )
        """
        DBAssert._check_sql_safety(sql)
        client = DBAssert._get_client()

        try:
            row_dict = client.query_one(sql, params=params, timeout=timeout)
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {sql[:100]} - {e}") from e

        if not row_dict:
            raise AssertionError(
                f"数据库查询无结果: {sql[:200]}",
                detail={"sql": sql, "params": params, "reason": "no_rows"},
            )

        # 逐字段比较
        mismatches = {}
        for field, expected in expected_dict.items():
            actual = row_dict.get(field)
            if _normalize_db_value(actual) != _normalize_db_value(expected):
                mismatches[field] = {
                    "expected": expected,
                    "actual": actual,
                }

        if mismatches:
            msg_lines = [f"数据库字段不符 ({len(mismatches)} 处):"]
            for field, diff in mismatches.items():
                msg_lines.append(
                    f"  {field}: 期望 {diff['expected']}，实际 {diff['actual']}"
                )
            raise AssertionError(
                "\n".join(msg_lines),
                detail={
                    "sql": sql,
                    "params": params,
                    "mismatches": mismatches,
                },
            )

        logger.info(f"[DBAssert] ✅ db_row 通过: {len(expected_dict)} 个字段")

    # ============================================================
    # 5. 安全清理辅助（非断言，配合 TrashBin 使用）
    # ============================================================
    @staticmethod
    def execute(sql: str, params: Optional[tuple] = None) -> int:
        """
        执行写操作 SQL（用于测试数据清理）

        :return: 受影响行数
        注意：需要显式 COMMIT（DBClient 内部处理）
        """
        DBAssert._check_sql_safety(sql)
        client = DBAssert._get_client()
        try:
            affected = client.execute(sql, params=params)
            logger.info(f"[DBAssert] SQL 执行成功: {affected} 行受影响 - {sql[:80]}")
            return affected
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {sql[:100]} - {e}") from e


# ============================================================
# 内部辅助：DB 值类型归一化
# ============================================================
def _normalize_db_value(value: Any) -> Any:
    """
    数据库返回值归一化

    场景：
        - Decimal('199.80') vs 199.80（浮点）
        - datetime(2026,1,5) vs '2026-01-05'（字符串）
        - b'abc' vs 'abc'（字节串）
    """
    if value is None:
        return None

    # Decimal → float
    if type(value).__name__ == "Decimal":
        return float(value)

    # datetime / date → 字符串
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "hour") \
               else value.strftime("%Y-%m-%d")

    # bytes → str
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value