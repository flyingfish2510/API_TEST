# -*- coding: utf-8 -*-
"""
core/engine/yaml_runner.py
--------------------------
用例执行编排引擎（YamlRunner）

职责：
    1. 接收 YamlLoader 解析好的结构化用例数据
    2. 编排 setup -> steps -> teardown 的生命周期
    3. 串联各个核心组件：
       - VariableResolver（变量解析）
       - EnvGuard（安全检查）
       - RequestBuilder（请求组装与发送）
       - Extractor（数据提取）
       - AssertFactory（断言校验）
    4. 与 Allure 报告集成（记录步骤、附加请求/响应日志）
    5. 处理异常（区分业务失败与框架崩溃）并自动清理环境

依赖：
    pip install allure-pytest
"""
import time
import logging
import traceback
from typing import Any, Dict, List, Optional

import allure

from common.client import HttpClient
from common.context import context
from common.exceptions import API_TESTException, SafetyBlockedError
from config import settings

from .variable_resolver import VariableResolver
from .env_guard import EnvGuard
from .request_builder import RequestBuilder
from .extractor import Extractor

from core.assertions import AssertFactory
from core.cleanup import TrashBin

logger = logging.getLogger(__name__)


class YamlRunner:
    """
    YAML 用例执行引擎（静态方法）
    """

    @classmethod
    def run_case(cls, case_data: Dict[str, Any], api_client: HttpClient) -> None:
        """
        执行完整的 YAML 用例

        :param case_data:  YamlLoader 返回的结构化数据
        :param api_client: 共享的 HttpClient 实例
        """
        case_name = case_data.get("case_name", "Unknown Case")
        file_path = case_data.get("file", "")

        # 1. 设置 Allure 报告的用例信息
        cls._setup_allure(case_data)

        # 2. 记录开始时间
        start_time = time.time()
        logger.info(f"▶️ 开始执行用例: {case_name} (文件: {file_path})")

        try:
            with allure.step("Phase 1: Setup 前置准备"):
                cls._run_phase(case_data.get("setup", []), api_client, "setup")

            with allure.step("Phase 2: Test 业务测试"):
                cls._run_phase(case_data.get("steps", []), api_client, "test")

            with allure.step("Phase 3: Teardown 后置清理"):
                cls._run_phase(case_data.get("teardown", []), api_client, "teardown")

            elapsed = round(time.time() - start_time, 2)
            logger.info(f"✅ 用例执行完成: {case_name} (耗时 {elapsed}s)")
            allure.attach(f"总耗时: {elapsed}s", name="Execution Time")

        except Exception as e:
            # 如果异常已经被记录到 Allure，直接抛出，不再重复记录
            logger.error(f"❌ 用例执行失败: {case_name} - {e}")

            # 即使失败，也尽量执行 teardown（容错设计）
            if case_data.get("teardown"):
                with allure.step("Phase 3: Teardown (失败后兜底清理)"):
                    try:
                        cls._run_phase(case_data.get("teardown", []), api_client, "teardown")
                    except Exception as te:
                        logger.warning(f"兜底清理失败（忽略）: {te}")

            raise  # 重新抛出，让 pytest 捕获并标记用例失败

    # ============================================================
    # 阶段执行器
    # ============================================================
    @classmethod
    def _run_phase(
            cls,
            phase_steps: List[Dict[str, Any]],
            api_client: HttpClient,
            phase_name: str,
    ) -> None:
        """
        执行一个阶段的多个步骤

        说明：
            YamlLoader 已保证 phase_steps 是「扁平的步骤列表」，
            每个元素形如 {"name": ..., "request": {...}}，此处无需再递归钻取。
            但仍保留对旧格式 [{"setup": [...]}] 的兼容兜底。
        """
        if not phase_steps:
            return

        flat_steps: List[Dict[str, Any]] = []

        def _normalize(items):
            for item in items:
                if not isinstance(item, dict):
                    continue
                # 兼容历史写法：{"test": {...}} / {"setup": [...]} 等包装
                if len(item) == 1:
                    only_key = next(iter(item))
                    if only_key in ("test", "setup", "teardown") and isinstance(item[only_key], (dict, list)):
                        _normalize(item[only_key] if isinstance(item[only_key], list) else [item[only_key]])
                        continue
                flat_steps.append(item)

        _normalize(phase_steps)

        logger.info(
            f"[_run_phase] {phase_name} 阶段: 原始 {len(phase_steps)} 项 → 展平后 {len(flat_steps)} 个步骤"
        )

        for step_data in flat_steps:
            # 严格校验
            if not isinstance(step_data, dict):
                logger.warning(f"[_run_phase] 跳过非 dict: {type(step_data)}")
                continue
            # 支持"纯变量步骤"：只有 variables、没有 request，用于造数/设置上下文
            if "request" not in step_data:
                if step_data.get("variables"):
                    cls._apply_variables(step_data, phase_name)
                    continue
                logger.warning(f"[_run_phase] 跳过无 request 的步骤: {step_data.get('name', '?')}")
                continue
            if not isinstance(step_data.get("request"), dict):
                logger.warning(f"[_run_phase] 跳过 request 非 dict 的步骤: {step_data.get('name', '?')}")
                continue
            if not step_data["request"].get("url"):
                logger.warning(
                    f"[_run_phase] 跳过无 url 的步骤: {step_data.get('name', '?')}"
                )
                continue

            cls._run_step(step_data, api_client, phase_name)

    # ============================================================
    # 单步执行器（核心）
    # ============================================================
    @classmethod
    def _run_step(
            cls,
            step_data: Dict[str, Any],
            api_client: HttpClient,
            phase_name: str,
    ) -> None:
        """
        执行单个请求步骤

        流程：
            1. VariableResolver 解析变量
            2. 打印请求日志
            3. EnvGuard 安全检查
            4. RequestBuilder 组装并发送请求
            5. Extractor 提取数据
            6. AssertFactory 执行断言
        """
        step_name = step_data.get("name", "Unnamed Step")

        with allure.step(f"[{phase_name}] {step_name}"):
            logger.info(f"  └── 执行步骤: {step_name}")

            # ---------- 0. 步骤级变量注入（可选） ----------
            if step_data.get("variables"):
                cls._apply_variables(step_data, phase_name)

            # ---------- 1. 变量解析 ----------
            try:
                resolved_request = VariableResolver.resolve(step_data.get("request", {}))
                resolved_extract = VariableResolver.resolve(step_data.get("extract", {}))
                # 注意：validate 里的期望值也可能有变量，需要解析
                resolved_validators = VariableResolver.resolve(step_data.get("validate", []))
            except Exception as e:
                cls._attach_error("变量解析失败", e)
                raise

            method = resolved_request.get("method", "GET").upper()
            url = resolved_request.get("url", "")

            # 附加请求参数到 Allure（脱敏由 HttpClient 负责）
            allure.attach(
                str(resolved_request),
                name=f"Request: {method} {url}",
                attachment_type=allure.attachment_type.TEXT,
            )

            # ---------- 2. 安全检查 (EnvGuard) ----------
            try:
                EnvGuard.check(method, url, resolved_request.get("json") or resolved_request.get("data"))
            except SafetyBlockedError as e:
                # 安全拦截：附加信息、标记 Skipped
                cls._attach_error("安全拦截", e)
                import pytest
                pytest.skip(str(e))
                return

            # ---------- 3. 组装并发发送 (RequestBuilder) ----------
            try:
                # 注入全局 Headers（例如 Auth Token）
                global_headers = cls._get_global_headers()

                response = RequestBuilder.build_and_send(
                    api_client=api_client,
                    request_data=resolved_request,
                    global_headers=global_headers,
                )

                # 附加响应到 Allure
                cls._attach_response(response)

            except Exception as e:
                cls._attach_error("请求发送失败", e)
                raise

            # ---------- 4. 数据提取 (Extractor) ----------
            if resolved_extract:
                try:
                    with allure.step("数据提取"):
                        extracted = Extractor.extract(response, resolved_extract)
                        if extracted:
                            allure.attach(
                                str(extracted),
                                name="Extracted Variables",
                                attachment_type=allure.attachment_type.JSON,
                            )
                except Exception as e:
                    cls._attach_error("数据提取失败", e)
                    raise

            # ---------- 5. 断言校验 (AssertFactory) ----------
            if resolved_validators:
                try:
                    with allure.step("断言校验"):
                        AssertFactory.validate(response, resolved_validators)
                except Exception as e:
                    cls._attach_error("断言校验失败", e)
                    raise

    # ============================================================
    # 变量注入（造数/设置上下文）
    # ============================================================
    @classmethod
    def _apply_variables(cls, step_data: Dict[str, Any], phase_name: str) -> None:
        """
        将步骤中声明的 variables 解析后写入 case 作用域上下文

        YAML 写法：
            - test:
                name: "[前置] 生成测试数据"
                variables:
                  test_username: "{{faker.unique_user_name}}"
                  test_phone: "{{faker.unique_phone}}"
        """
        raw_vars = step_data.get("variables") or {}
        if not isinstance(raw_vars, dict):
            logger.warning(f"[_run_phase] variables 非 dict，已跳过: {raw_vars}")
            return

        try:
            resolved = VariableResolver.resolve(raw_vars)
        except Exception as e:
            logger.error(f"[_run_phase] 变量注入解析失败: {e}")
            raise

        for k, v in resolved.items():
            context.set(k, v, scope="case")
        logger.info(
            f"[_run_phase] 已注入 {len(resolved)} 个变量: {list(resolved.keys())}"
        )

    # ============================================================
    # Allure 集成
    # ============================================================
    @staticmethod
    def _setup_allure(case_data: Dict[str, Any]) -> None:
        """动态设置当前用例的 Allure 元数据"""
        allure.dynamic.epic(case_data.get("module", "Unknown Module").capitalize())
        allure.dynamic.feature(case_data.get("case_name"))

        priority = case_data.get("priority", "P2").upper()
        severity_map = {
            "P0": allure.severity_level.BLOCKER,
            "P1": allure.severity_level.CRITICAL,
            "P2": allure.severity_level.NORMAL,
            "P3": allure.severity_level.MINOR,
        }
        allure.dynamic.severity(severity_map.get(priority, allure.severity_level.NORMAL))

        for tag in case_data.get("tags", []):
            allure.dynamic.tag(tag)

        allure.dynamic.title(case_data.get("case_name", "Unknown Case"))

    @staticmethod
    def _attach_response(response: Any) -> None:
        """将响应结果附加到 Allure 报告"""
        elapsed_ms = getattr(response, "elapsed", None)
        elapsed_str = f"{elapsed_ms.total_seconds() * 1000:.2f}ms" if elapsed_ms else "unknown"

        headers_str = "\n".join(f"{k}: {v}" for k, v in response.headers.items())

        try:
            body = response.text
            # 如果超过 5000 字符，截断
            if len(body) > 5000:
                body = body[:5000] + "\n... (truncated)"
        except Exception:
            body = "<无法读取响应体>"

        content = (
            f"Status Code: {response.status_code}\n"
            f"Elapsed: {elapsed_str}\n"
            f"--- Headers ---\n{headers_str}\n"
            f"--- Body ---\n{body}"
        )

        allure.attach(
            content,
            name=f"Response: {response.status_code}",
            attachment_type=allure.attachment_type.TEXT,
        )

    @staticmethod
    def _attach_error(action: str, exc: Exception) -> None:
        """将异常信息附加到 Allure 报告"""
        error_type = type(exc).__name__
        error_msg = str(exc)

        # 追加堆栈信息（如果是未预期异常）
        tb_str = ""
        if not isinstance(exc, API_TESTException):
            tb_str = "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        content = f"Action: {action}\nError: {error_type}: {error_msg}{tb_str}"

        allure.attach(
            content,
            name=f"Error: {action}",
            attachment_type=allure.attachment_type.TEXT,
        )

    # ============================================================
    # 辅助方法
    # ============================================================
    @staticmethod
    def _get_global_headers() -> Dict[str, str]:
        """从 context 读取全局鉴权 Header（如 token）"""
        headers = {}
        token = context.get("token", scope="global")
        if token:
            headers["token"] = str(token)

        # 也可以读取 Cookie（虽然 requests.Session 会自动管理）
        cookies = context.get("cookies", scope="global")
        if cookies and isinstance(cookies, dict):
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            headers["Cookie"] = cookie_str

        return headers