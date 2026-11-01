# -*- coding: utf-8 -*-
"""
core/models/response_model.py
响应数据模型（最小可用版）
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CaseStatus(str, Enum):
    """用例执行状态"""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BROKEN = "broken"


class ResponseData(BaseModel):
    """HTTP 响应数据"""
    model_config = {"extra": "allow"}

    status_code: int = 0
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Any = None
    text: str = ""
    elapsed_ms: float = 0.0
    url: str = ""
    cookies: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_response(cls, response: Any) -> "ResponseData":
        if response is None:
            raise ValueError("响应对象不能为 None")

        body = None
        text = ""
        try:
            text = response.text or ""
            body = response.json()
        except Exception:
            body = None

        elapsed_ms = 0.0
        elapsed = getattr(response, "elapsed", None)
        if elapsed is not None:
            try:
                elapsed_ms = round(elapsed.total_seconds() * 1000, 2)
            except Exception:
                elapsed_ms = 0.0

        cookies = {}
        rc = getattr(response, "cookies", None)
        if rc is not None:
            try:
                cookies = dict(rc)
            except Exception:
                cookies = {}

        return cls(
            status_code=response.status_code,
            headers=dict(response.headers or {}),
            body=body,
            text=text,
            elapsed_ms=elapsed_ms,
            url=getattr(response, "url", ""),
            cookies=cookies,
        )


class AssertResult(BaseModel):
    """单条断言结果"""
    model_config = {"extra": "forbid"}

    assert_type: str
    passed: bool
    message: str = ""
    expected: Any = None
    actual: Any = None
    path: Optional[str] = None
    elapsed_ms: float = 0.0

    @classmethod
    def success(cls, assert_type: str, message: str = "断言通过", **kwargs):
        return cls(assert_type=assert_type, passed=True, message=message, **kwargs)

    @classmethod
    def fail(cls, assert_type: str, message: str, **kwargs):
        return cls(assert_type=assert_type, passed=False, message=message, **kwargs)


class CaseResult(BaseModel):
    """用例执行结果汇总"""
    model_config = {"extra": "forbid"}

    case_name: str
    file: str = ""
    module: str = "unknown"
    priority: str = "P2"
    tags: List[str] = Field(default_factory=list)

    status: CaseStatus = CaseStatus.PASSED
    start_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    end_time: Optional[str] = None
    duration: float = 0.0

    total_steps: int = 0
    passed_steps: int = 0
    failed_steps: int = 0

    total_asserts: int = 0
    passed_asserts: int = 0
    failed_asserts: int = 0

    assert_results: List[AssertResult] = Field(default_factory=list)
    error_message: Optional[str] = None
    trace_id: Optional[str] = None

    def mark_finished(self) -> None:
        self.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            start = datetime.strptime(self.start_time, "%Y-%m-%d %H:%M:%S")
            self.duration = round((datetime.now() - start).total_seconds(), 2)
        except Exception:
            self.duration = 0.0

        if self.failed_asserts > 0 or self.failed_steps > 0:
            self.status = CaseStatus.FAILED
        elif self.status != CaseStatus.BROKEN:
            self.status = CaseStatus.PASSED

    def add_assert_result(self, result: AssertResult) -> None:
        self.assert_results.append(result)
        self.total_asserts += 1
        if result.passed:
            self.passed_asserts += 1
        else:
            self.failed_asserts += 1

    @property
    def is_passed(self) -> bool:
        return self.status == CaseStatus.PASSED

    @property
    def pass_rate(self) -> float:
        if self.total_asserts == 0:
            return 100.0
        return round(self.passed_asserts / self.total_asserts * 100, 2)