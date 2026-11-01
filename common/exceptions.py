# -*- coding: utf-8 -*-
"""
common/exceptions.py
--------------------
API_TEST 框架自定义异常体系

设计原则：
    1. 所有异常都继承自 API_TESTException（统一捕获）
    2. 按"问题归属"划分大类（框架侧 / 环境侧 / 业务侧）
    3. 每个异常绑定对应的 Jenkins 退出码（Exit Code）
    4. 区分 Failed（业务断言失败）与 Broken（框架/环境错误）

退出码映射（供 CI/CD 判定）：
    0 - 全部通过
    1 - 存在业务失败（断言失败、用例失败）
    2 - 框架内部错误（配置错误、依赖缺失、代码 Bug）
    3 - 环境不可达（服务未启动、网络不通、DB 连不上）
    4 - 安全拦截（生产环境写操作被阻止）
"""
from typing import Optional, Any, Dict


# ============================================================
# 一、基础异常（所有异常的父类）
# ============================================================
class API_TESTException(Exception):
    """
    框架所有异常的基类

    属性：
        code:        Jenkins 退出码（供 CI 判定）
        category:    异常分类（framework / environment / business / safety）
        detail:      附加信息（字典，供日志/报告使用）
        original:    原始异常（用于异常链保留）
    """

    code: int = 2  # 默认归为框架错误
    category: str = "framework"  # 默认分类

    def __init__(
            self,
            message: str = "",
            *,
            code: Optional[int] = None,
            detail: Optional[Dict[str, Any]] = None,
            original: Optional[Exception] = None,
    ):
        self.message = message
        self.detail = detail or {}
        self.original = original
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """序列化（供 JSON 报告输出）"""
        return {
            "type": self.__class__.__name__,
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }

    def __str__(self) -> str:
        base = f"[{self.category}] {self.__class__.__name__}: {self.message}"
        if self.detail:
            base += f" | detail={self.detail}"
        return base


# ============================================================
# 二、配置类异常（退出码 2）
# ============================================================
class ConfigError(API_TESTException):
    """配置相关错误（YAML 语法错误、环境配置缺失、字段非法等）"""
    code = 2
    category = "config"


class EnvConfigNotFoundError(ConfigError):
    """环境配置文件不存在（如 config/env/test.yaml 缺失）"""


class YamlSyntaxError(ConfigError):
    """YAML 用例或配置文件语法错误"""


class VariableNotResolvedError(ConfigError):
    """
    变量解析失败（如 {{undefined_var}} 未定义）
    通常意味着 YAML 编写错误或上下文数据缺失
    """

    def __init__(self, var_name: str, available_keys: Optional[list] = None):
        detail = {"var_name": var_name}
        if available_keys:
            detail["available_keys"] = available_keys
        super().__init__(
            f"变量未定义或无法解析: {var_name}",
            detail=detail,
        )


# ============================================================
# 三、环境类异常（退出码 3）
# ============================================================
class EnvironmentError(API_TESTException):
    """环境不可达类错误（服务未启动、网络不通等）"""
    code = 3
    category = "environment"


class NetworkError(EnvironmentError):
    """
    网络层异常
    - 连接拒绝（Connection refused）
    - DNS 解析失败
    - SSL 握手失败
    """


class TimeoutError(NetworkError):
    """
    超时错误
    - 连接超时（ConnectTimeout）
    - 读取超时（ReadTimeout）
    """


class ServiceUnavailableError(EnvironmentError):
    """
    服务不可达（HTTP 502/503/504，健康检查失败）
    """


class DatabaseError(EnvironmentError):
    """数据库相关错误（连接失败、SQL 语法错误、断言查询失败）"""


class RedisError(EnvironmentError):
    """Redis 相关错误"""


# ============================================================
# 四、认证类异常（归为环境类，退出码 3）
# ============================================================
class AuthError(EnvironmentError):
    """
    认证失败
    - 登录接口返回非 200
    - Token 提取失败
    - 响应格式非预期
    """


class TokenExpiredError(AuthError):
    """
    Token 已过期，需要重新获取
    （框架会自动刷新，此异常用于强制刷新场景）
    """


class CredentialMissingError(AuthError):
    """
    凭证缺失
    - config/env/*.yaml 中未配置该角色
    - 环境变量中缺少凭证
    """


class UnauthorizedError(AuthError):
    """
    未授权（HTTP 401）
    通常代表 Token 失效或未传递
    """

    def __init__(self, url: str = "", message: str = "未授权"):
        super().__init__(
            message or f"接口返回 401 未授权: {url}",
            detail={"url": url},
        )


class ForbiddenError(AuthError):
    """权限不足（HTTP 403）"""


# ============================================================
# 五、业务类异常（退出码 1）
# ============================================================
class BusinessError(API_TESTException):
    """
    业务失败（断言不通过、业务码异常、数据不一致）
    这是最常见的异常类型
    """
    code = 1
    category = "business"


class ApiAssertionError(BusinessError):
    """
    断言失败
    - 状态码不符
    - JSONPath 提取值不符
    - Schema 校验失败
    - 数据库断言失败
    """

    def __init__(
            self,
            message: str,
            *,
            path: Optional[str] = None,
            expected: Any = None,
            actual: Any = None,
            detail: Optional[Dict[str, Any]] = None,
    ):
        # 合并显式 detail 与 path/expected/actual 便捷字段
        merged: Dict[str, Any] = dict(detail or {})
        if path:
            merged.setdefault("path", path)
        if expected is not None:
            merged.setdefault("expected", expected)
        if actual is not None:
            merged.setdefault("actual", actual)
        super().__init__(message, detail=merged)

    @classmethod
    def from_eq(cls, path: str, expected: Any, actual: Any) -> "ApiAssertionError":
        """便捷构造：eq 断言失败"""
        return cls(
            f"{path} 期望 {expected}，实际 {actual}",
            path=path, expected=expected, actual=actual,
        )

    @classmethod
    def from_contains(cls, path: str, substring: Any, actual: Any) -> "ApiAssertionError":
        """便捷构造：contains 断言失败"""
        return cls(
            f"{path} 值 {actual} 不含 {substring}",
            path=path, expected=f"包含 {substring}", actual=actual,
        )


class SchemaValidationError(BusinessError):
    """JSON Schema 契约校验失败（响应结构不符合预期）"""

    def __init__(self, message: str, schema_name: str = "", errors: Optional[list] = None):
        super().__init__(
            message,
            detail={"schema": schema_name, "errors": errors or []},
        )


class SnapshotMismatchError(BusinessError):
    """基线快照对比失败（响应结构发生变化）"""

    def __init__(self, message: str, diff: Optional[dict] = None):
        super().__init__(message, detail={"diff": diff or {}})


class PerformanceError(BusinessError):
    """性能断言失败（响应时间超阈值）"""

    def __init__(self, path: str, elapsed_ms: float, threshold_ms: float):
        super().__init__(
            f"{path} 响应耗时 {elapsed_ms}ms 超过阈值 {threshold_ms}ms",
            detail={"path": path, "elapsed_ms": elapsed_ms, "threshold_ms": threshold_ms},
        )


class IdempotencyError(BusinessError):
    """幂等性校验失败（重复请求产生了不同结果或产生了重复副作用）"""


# ============================================================
# 六、安全类异常（退出码 4）
# ============================================================
class SafetyBlockedError(API_TESTException):
    """
    生产环境安全拦截
    - 尝试在 prod 环境执行写操作（POST/PUT/DELETE）
    - 尝试访问 forbidden_urls 清单中的敏感接口
    """
    code = 4
    category = "safety"

    def __init__(self, url: str, method: str, reason: str = ""):
        super().__init__(
            f"生产环境拦截: {method} {url} — {reason or '禁止写操作'}",
            detail={"url": url, "method": method, "reason": reason},
        )


# ============================================================
# 七、执行策略类异常（退出码 2）
# ============================================================
class StrategyError(API_TESTException):
    """执行策略类错误"""
    code = 2
    category = "strategy"


class RetryExhaustedError(StrategyError):
    """重试次数耗尽（网络重试多次仍失败）"""

    def __init__(self, url: str, attempts: int):
        super().__init__(
            f"重试 {attempts} 次后仍然失败: {url}",
            detail={"url": url, "attempts": attempts},
        )


class IncrementalPlanError(StrategyError):
    """增量执行计划解析失败（git diff 计算结果异常）"""


# ============================================================
# 八、Mock / 数据类异常（退出码 2）
# ============================================================
class MockError(API_TESTException):
    """Mock 服务相关错误"""
    code = 2
    category = "mock"


class MockServerStartError(MockError):
    """Mock Server 启动失败（端口占用、依赖缺失）"""


class MockRuleNotFoundError(MockError):
    """Mock 规则文件未找到或匹配失败"""


class DataFactoryError(API_TESTException):
    """测试数据构造/清理失败"""
    code = 2
    category = "data"


class CleanupError(DataFactoryError):
    """
    数据清理失败
    注意：清理失败通常不阻断主流程（避免误报），只记录 WARN
    """


# ============================================================
# 九、异常分类与退出码映射（供 CI 使用）
# ============================================================
EXIT_CODE_MAP = {
    "success": 0,
    "business": 1,  # 断言失败、业务错误
    "framework": 2,  # 配置/代码/依赖问题
    "config": 2,
    "strategy": 2,
    "mock": 2,
    "data": 2,
    "environment": 3,  # 网络、DB、Service 不可达
    "auth": 3,
    "safety": 4,  # 生产环境写操作被拦截
}


def resolve_exit_code(exc: Optional[BaseException] = None) -> int:
    """
    根据异常推断 Jenkins 退出码

    :param exc: 捕获到的异常（None 表示成功）
    :return:    0/1/2/3/4
    """
    if exc is None:
        return 0
    if isinstance(exc, API_TESTException):
        return exc.code
    # 未识别的异常视为框架错误
    return 2


# ============================================================
# 十、公开接口
# ============================================================
__all__ = [
    # 基类
    "API_TESTException",
    # 配置类
    "ConfigError",
    "EnvConfigNotFoundError",
    "YamlSyntaxError",
    "VariableNotResolvedError",
    # 环境类
    "EnvironmentError",
    "NetworkError",
    "TimeoutError",
    "ServiceUnavailableError",
    "DatabaseError",
    "RedisError",
    # 认证类
    "AuthError",
    "TokenExpiredError",
    "CredentialMissingError",
    "UnauthorizedError",
    "ForbiddenError",
    # 业务类
    "BusinessError",
    "ApiAssertionError",
    "SchemaValidationError",
    "SnapshotMismatchError",
    "PerformanceError",
    "IdempotencyError",
    # 安全类
    "SafetyBlockedError",
    # 执行策略类
    "StrategyError",
    "RetryExhaustedError",
    "IncrementalPlanError",
    # Mock / 数据类
    "MockError",
    "MockServerStartError",
    "MockRuleNotFoundError",
    "DataFactoryError",
    "CleanupError",
    # 工具
    "EXIT_CODE_MAP",
    "resolve_exit_code",
]