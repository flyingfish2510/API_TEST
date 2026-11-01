# -*- coding: utf-8 -*-
"""
common/context.py
-----------------
线程安全的测试上下文管理（TestContext）

职责：
    1. 提供全局数据共享（类似 ThreadLocal）
    2. 隔离用例级别的数据（pytest 并发时各线程数据互不干扰）
    3. 支持按作用域（global / case）读写
    4. 支持自动清理，防止内存泄漏

层级设计：
    - global:   跨用例共享（如 env 配置、全局 trace_id）
    - case:     用例级别共享（如当前用例的 extract 变量、响应对象）

用法：
    from common.context import context
    context.set("token", "xxx")                    # 默认写 global
    context.set("order_no", "DD123", scope="case") # 写当前用例
    token = context.get("token")
    context.push("test_login")                     # 用例开始时压栈
    context.pop()                                  # 用例结束时出栈并清理 case 数据
"""
import threading
from typing import Any, Dict, Optional


class TestContext:
    """
    测试上下文（线程安全）
    """

    def __init__(self):
        # 使用 threading.local 实现线程隔离
        self._local = threading.local()

    def _get_data(self) -> Dict[str, Any]:
        """获取当前线程的数据容器（懒加载）"""
        if not hasattr(self._local, "data"):
            self._local.data = {
                "global": {},  # 全局作用域（整个进程共享，跨用例）
                "case": {},  # 用例作用域（当前线程当前用例）
                "stack": [],  # 用例栈（支持嵌套用例）
            }
        return self._local.data

    # ============================================================
    # 用例栈管理
    # ============================================================
    def push(self, test_name: str) -> None:
        """
        用例开始时压栈
        作用：
            1. 记录当前正在运行的用例名（便于日志、报告）
            2. 为后续 pop 自动清理 case 数据做准备
        """
        data = self._get_data()
        data["stack"].append(test_name)
        # 每次进入新用例时重置 case 作用域（防数据串扰）
        data["case"] = {}

    def pop(self) -> Optional[str]:
        """
        用例结束时出栈，并清理 case 数据
        :return: 被弹出的用例名
        """
        data = self._get_data()
        if not data["stack"]:
            return None
        test_name = data["stack"].pop()
        data["case"].clear()  # 清理用例级数据，防止内存泄漏
        return test_name

    def current_case(self) -> Optional[str]:
        """获取当前正在运行的用例名"""
        data = self._get_data()
        return data["stack"][-1] if data["stack"] else None

    # ============================================================
    # 数据读写
    # ============================================================
    def set(self, key: str, value: Any, scope: str = "global") -> None:
        """
        写入数据

        :param key:   变量名
        :param value: 变量值
        :param scope: 'global'（默认，跨用例）/ 'case'（仅当前用例）
        """
        if scope not in ("global", "case"):
            raise ValueError(f"不支持的 scope: {scope}，可选 'global' 或 'case'")

        data = self._get_data()
        data[scope][key] = value

    def get(self, key: str, default: Any = None, scope: str = "global") -> Any:
        """
        读取数据

        优先级：
            - scope='global'：只查 global
            - scope='case'：只查 case
            - scope='auto'：先查 case，再查 global（常用）
        """
        data = self._get_data()

        if scope == "global":
            return data["global"].get(key, default)
        elif scope == "case":
            return data["case"].get(key, default)
        elif scope == "auto":
            # 用例级优先（通常用例内生成的变量应覆盖全局）
            if key in data["case"]:
                return data["case"][key]
            return data["global"].get(key, default)
        else:
            raise ValueError(f"不支持的 scope: {scope}")

    def exists(self, key: str, scope: str = "auto") -> bool:
        """判断变量是否存在"""
        data = self._get_data()
        if scope == "auto":
            return key in data["case"] or key in data["global"]
        return key in data.get(scope, {})

    def delete(self, key: str, scope: str = "global") -> bool:
        """删除某个变量，返回是否删除成功"""
        data = self._get_data()
        if key in data.get(scope, {}):
            del data[scope][key]
            return True
        return False

    # ============================================================
    # 批量读写
    # ============================================================
    def update(self, items: Dict[str, Any], scope: str = "global") -> None:
        """批量写入"""
        data = self._get_data()
        if scope not in ("global", "case"):
            raise ValueError(f"不支持的 scope: {scope}")
        data[scope].update(items)

    def get_all(self, scope: str = "auto") -> Dict[str, Any]:
        """
        获取所有数据（合并 case + global）
        :return: 合并后的字典（case 数据会覆盖 global 中的同名键）
        """
        data = self._get_data()
        if scope == "auto":
            merged = dict(data["global"])
            merged.update(data["case"])
            return merged
        return dict(data.get(scope, {}))

    # ============================================================
    # 清理
    # ============================================================
    def clear(self, scope: str = "auto") -> None:
        """
        清空数据

        :param scope: 'global' / 'case' / 'auto'（清空所有）
        """
        data = self._get_data()
        if scope == "global":
            data["global"].clear()
        elif scope == "case":
            data["case"].clear()
        elif scope == "auto":
            data["global"].clear()
            data["case"].clear()
            data["stack"].clear()
        else:
            raise ValueError(f"不支持的 scope: {scope}")

    def reset(self) -> None:
        """彻底重置当前线程的上下文（用于测试隔离）"""
        self._local.data = {
            "global": {},
            "case": {},
            "stack": [],
        }

    # ============================================================
    # 便捷属性
    # ============================================================
    def __contains__(self, key: str) -> bool:
        return self.exists(key)

    def __repr__(self) -> str:
        data = self._get_data()
        return (
            f"<TestContext case={self.current_case()} "
            f"global_keys={list(data['global'].keys())} "
            f"case_keys={list(data['case'].keys())}>"
        )


# ============================================================
# 全局单例（框架内直接 from common import context 使用）
# ============================================================
context = TestContext()