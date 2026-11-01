# -*- coding: utf-8 -*-
"""
common/utils/time_util.py
-------------------------
时间处理工具类（TimeUtil）

职责：
    1. 获取当前时间戳（秒/毫秒）
    2. 格式化为标准字符串（适配接口文档要求的 %Y-%m-%d %H:%M:%S）
    3. 日期偏移计算（如生成有效期 dates: 2023-12-31）
    4. 字符串解析为 datetime

常用格式常量：
    DATE_FORMAT      = "%Y-%m-%d"
    DATETIME_FORMAT  = "%Y-%m-%d %H:%M:%S"
"""
import time
import datetime
import logging
from typing import Union, Optional

logger = logging.getLogger(__name__)


class TimeUtil:
    """时间工具类（全静态方法）"""

    # 常用格式常量
    DATE_FORMAT = "%Y-%m-%d"
    DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    TIME_FORMAT = "%H:%M:%S"
    COMPACT_DATE_FORMAT = "%Y%m%d"

    # ============================================================
    # 1. 当前时间获取
    # ============================================================
    @staticmethod
    def now_timestamp(unit: str = "s") -> int:
        """
        获取当前时间戳

        :param unit: 's' 返回秒级时间戳；'ms' 返回毫秒级时间戳
        :return:     int 型时间戳
        """
        if unit == "s":
            return int(time.time())
        elif unit == "ms":
            return int(time.time() * 1000)
        raise ValueError(f"不支持的时间单位: {unit}，可选 's' 或 'ms'")

    @staticmethod
    def now_str(fmt: Optional[str] = None) -> str:
        """
        获取当前时间字符串

        :param fmt: 格式，默认使用 DATETIME_FORMAT
        """
        return datetime.datetime.now().strftime(fmt or TimeUtil.DATETIME_FORMAT)

    @staticmethod
    def today_str(fmt: Optional[str] = None) -> str:
        """获取今天的日期字符串，默认 %Y-%m-%d"""
        return datetime.date.today().strftime(fmt or TimeUtil.DATE_FORMAT)

    # ============================================================
    # 2. 时间格式化
    # ============================================================
    @staticmethod
    def format_time(
            dt: Union[datetime.datetime, str, int],
            fmt: Optional[str] = None,
    ) -> str:
        """
        将任意时间对象格式化为字符串

        :param dt:  datetime 对象 / 字符串（需匹配默认格式） / 时间戳（秒）
        :param fmt: 目标格式
        """
        fmt = fmt or TimeUtil.DATETIME_FORMAT

        if isinstance(dt, str):
            # 尝试解析默认格式
            try:
                dt = datetime.datetime.strptime(dt, TimeUtil.DATETIME_FORMAT)
            except ValueError:
                raise ValueError(f"无法解析时间字符串: {dt}")
        elif isinstance(dt, (int, float)):
            dt = datetime.datetime.fromtimestamp(dt)

        if not isinstance(dt, datetime.datetime):
            raise TypeError(f"不支持的时间类型: {type(dt)}")

        return dt.strftime(fmt)

    @staticmethod
    def to_timestamp(dt: Union[datetime.datetime, str], unit: str = "s") -> int:
        """
        将 datetime 对象或字符串转换为时间戳

        :param dt:   datetime 对象 或 '2023-12-31 23:59:59' 字符串
        :param unit: 's' 或 'ms'
        """
        if isinstance(dt, str):
            dt = datetime.datetime.strptime(dt, TimeUtil.DATETIME_FORMAT)

        if not isinstance(dt, datetime.datetime):
            raise TypeError(f"不支持的时间类型: {type(dt)}")

        ts = dt.timestamp()
        return int(ts) if unit == "s" else int(ts * 1000)

    # ============================================================
    # 3. 时间偏移计算（生成未来/过去日期）
    # ============================================================
    @staticmethod
    def offset_days(days: int, fmt: Optional[str] = None, base_time: Optional[datetime.datetime] = None) -> str:
        """
        相对今天偏移 days 天，返回字符串

        :param days:      正数（未来），负数（过去）
        :param fmt:       目标格式
        :param base_time: 基准时间，默认今天

        示例：
            >>> TimeUtil.offset_days(30, fmt="%Y-%m-%d")   # 30天后
            '2023-12-31'
            >>> TimeUtil.offset_days(-7, fmt="%Y-%m-%d")   # 7天前
            '2023-11-25'
        """
        base_time = base_time or datetime.datetime.now()
        result = base_time + datetime.timedelta(days=days)
        return result.strftime(fmt or TimeUtil.DATE_FORMAT)

    @staticmethod
    def offset_hours(hours: int, fmt: Optional[str] = None) -> str:
        """相对当前时间偏移小时数"""
        result = datetime.datetime.now() + datetime.timedelta(hours=hours)
        return result.strftime(fmt or TimeUtil.DATETIME_FORMAT)

    @staticmethod
    def offset_minutes(minutes: int, fmt: Optional[str] = None) -> str:
        """相对当前时间偏移分钟数"""
        result = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
        return result.strftime(fmt or TimeUtil.DATETIME_FORMAT)

    # ============================================================
    # 4. 时间解析
    # ============================================================
    @staticmethod
    def parse(time_str: str, fmt: Optional[str] = None) -> datetime.datetime:
        """将字符串解析为 datetime 对象"""
        fmt = fmt or TimeUtil.DATETIME_FORMAT
        return datetime.datetime.strptime(time_str, fmt)

    # ============================================================
    # 5. 时间差计算（用于耗时断言）
    # ============================================================
    @staticmethod
    def diff_seconds(start: str, end: str, fmt: Optional[str] = None) -> float:
        """计算两个时间字符串之间的秒数差"""
        fmt = fmt or TimeUtil.DATETIME_FORMAT
        dt_start = datetime.datetime.strptime(start, fmt)
        dt_end = datetime.datetime.strptime(end, fmt)
        return (dt_end - dt_start).total_seconds()

    # ============================================================
    # 6. 通用调用入口（供 VariableResolver 使用）
    # ============================================================
    @classmethod
    def call(cls, method: str, *args, **kwargs):
        """
        供 VariableResolver 动态调用：
            {{time.now_timestamp()}}
            {{time.now_timestamp('ms')}}
            {{time.today_str()}}
            {{time.offset_days(30)}}
        """
        if not hasattr(cls, method):
            raise AttributeError(f"TimeUtil 无此方法: {method}")
        return getattr(cls, method)(*args, **kwargs)