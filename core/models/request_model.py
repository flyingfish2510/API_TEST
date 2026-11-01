# -*- coding: utf-8 -*-
"""
core/models/request_model.py
请求数据模型（最小可用版）
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ExtractSource(str, Enum):
    JSON = "json"
    REGEX = "regex"
    HEADER = "header"
    COOKIE = "cookie"


class RequestConfig(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    method: HttpMethod = HttpMethod.GET
    url: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    json_body: Optional[Any] = Field(default=None, alias="json")
    data: Optional[Any] = None
    files: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    timeout: Optional[float] = None
    allow_redirects: bool = True


class ExtractRule(BaseModel):
    model_config = {"extra": "allow"}

    source: ExtractSource = ExtractSource.JSON
    path: Optional[str] = None
    pattern: Optional[str] = None
    group: int = 1
    name: Optional[str] = None
    target: str = "text"


class AssertRule(BaseModel):
    model_config = {"extra": "allow"}

    assert_type: str
    args: List[Any] = Field(default_factory=list)


class TestStep(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    name: str = "Unnamed Step"
    request: Optional[RequestConfig] = None
    extract: Dict[str, Any] = Field(default_factory=dict)
    validators: List[Any] = Field(default_factory=list, alias="validate")
    ignore_errors: bool = False