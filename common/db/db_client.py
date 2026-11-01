# -*- coding: utf-8 -*-
"""
common/db/db_client.py
数据库客户端（最小可用版）
"""
import logging

logger = logging.getLogger(__name__)


class DBClient:
    """
    数据库客户端（基于 SQLAlchemy）

    用法：
        client = DBClient(host="127.0.0.1", port=3306,
                          user="root", password="123456",
                          database="test_db")
        rows = client.query("SELECT * FROM users WHERE id = %s", params=(1,))
        client.close()
    """

    def __init__(self, host, port, user, password, database,
                 charset="utf8mb4", pool_size=5):
        self.host = host
        self.port = port
        self.database = database

        # 延迟导入，避免没装 SQLAlchemy 时整个项目挂掉
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.pool import QueuePool
            from sqlalchemy.orm import sessionmaker
        except ImportError as e:
            raise ImportError(
                "DBClient 依赖 SQLAlchemy，请先 pip install SQLAlchemy pymysql"
            ) from e

        url = (
            f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
            f"?charset={charset}"
        )
        self._text = text
        self.engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.Session = sessionmaker(bind=self.engine)
        logger.debug(f"[DBClient] 初始化 {host}:{port}/{database}")

    def query(self, sql, params=None, timeout=5):
        """执行 SELECT，返回 List[Tuple]"""
        with self.engine.connect() as conn:
            result = conn.execute(self._text(sql), params or {})
            return [tuple(row) for row in result.fetchall()]

    def query_one(self, sql, params=None, timeout=5):
        """执行 SELECT，返回首行 Dict（无结果返回 None）"""
        with self.engine.connect() as conn:
            result = conn.execute(self._text(sql), params or {})
            row = result.fetchone()
            return dict(row._mapping) if row else None

    def execute(self, sql, params=None):
        """执行 INSERT/UPDATE/DELETE，返回受影响行数"""
        with self.engine.begin() as conn:
            result = conn.execute(self._text(sql), params or {})
            return result.rowcount

    def close(self):
        """关闭连接池"""
        try:
            self.engine.dispose()
        except Exception:
            pass