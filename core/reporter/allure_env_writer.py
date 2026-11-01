# -*- coding: utf-8 -*-
"""
core/reporter/allure_env_writer.py
----------------------------------
Allure 环境信息写入器（AllureEnvWriter）

职责：
    1. 生成 environment.properties（环境面板）
    2. 生成 executor.json（右上角执行器信息）
    3. 支持 CI 环境变量自动注入（Jenkins/GitLab/GitHub）
    4. 支持从框架配置中读取环境参数
    5. 幂等写入（重复执行会覆盖）

生成的产物位置：
    reports/allure-results/
    ├── environment.properties
    └── executor.json
"""
import os
import json
import logging
import socket
from datetime import datetime
from typing import Any, Dict, Optional

from common.utils import FileUtil
from config import settings, get_current_env

logger = logging.getLogger(__name__)


class AllureEnvWriter:
    """
    Allure 环境信息写入器（静态方法）
    """

    # 默认输出目录
    DEFAULT_RESULTS_DIR = "reports/allure-results"

    # ============================================================
    # 1. 环境信息写入
    # ============================================================
    @classmethod
    def write(cls, results_dir: Optional[str] = None) -> str:
        """
        写入 environment.properties

        :param results_dir: allure-results 目录（默认 reports/allure-results）
        :return:            写入文件的完整路径
        """
        results_dir = results_dir or cls.DEFAULT_RESULTS_DIR
        FileUtil.ensure_dir(results_dir)

        env_file = os.path.join(results_dir, "environment.properties")
        env_data = cls._collect_env()

        # 写入 properties 格式
        lines = [f"{k}={_escape_value(v)}" for k, v in env_data.items()]
        content = "\n".join(lines) + "\n"
        FileUtil.write_text(content, env_file)

        logger.info(f"[AllureEnv] environment.properties 写入: {env_file}")
        return env_file

    # ============================================================
    # 2. 执行器信息写入
    # ============================================================
    @classmethod
    def write_executor(cls, results_dir: Optional[str] = None) -> str:
        """
        写入 executor.json（报告右上角执行器信息）

        :param results_dir: allure-results 目录
        :return:            写入文件的完整路径
        """
        results_dir = results_dir or cls.DEFAULT_RESULTS_DIR
        FileUtil.ensure_dir(results_dir)

        executor_file = os.path.join(results_dir, "executor.json")
        executor_data = cls._collect_executor()

        FileUtil.dump_json(executor_data, executor_file)
        logger.info(f"[AllureEnv] executor.json 写入: {executor_file}")
        return executor_file

    # ============================================================
    # 3. 采集环境信息
    # ============================================================
    @classmethod
    def _collect_env(cls) -> Dict[str, Any]:
        """
        采集所有环境信息

        优先级：
            1. OS 环境变量（API_TEST_XXX）
            2. 框架配置（settings.get）
            3. CI 环境变量（Jenkins / GitLab）
            4. 系统信息（hostname / user）
        """
        from common import __version__ as common_version

        env_data = {
            # 基础环境
            "env": get_current_env(),
            "env_description": settings.get("description", ""),
            "base_url": settings.get("api.base_url", ""),
            "protocol": _infer_protocol(settings.get("api.base_url", "")),

            # 数据库
            "database_host": settings.get("database.host", "N/A"),
            "database_name": settings.get("database.db_name", "N/A"),

            # Redis
            "redis_host": settings.get("redis.host", "N/A"),

            # 框架信息
            "framework": "API_TEST",
            "framework_version": common_version,

            # 代码版本（CI 注入）
            "commit_id": _short_commit(_get_ci_var(["GIT_COMMIT", "CI_COMMIT_SHA", "GITHUB_SHA"])),
            "branch": _get_ci_var(["GIT_BRANCH", "CI_COMMIT_REF_NAME", "GITHUB_REF_NAME"]),
            "ci_platform": _detect_ci_platform(),

            # 构建信息
            "build_number": _get_ci_var(["BUILD_NUMBER", "CI_PIPELINE_IID", "GITHUB_RUN_NUMBER"]),
            "build_url": _get_ci_var(["BUILD_URL", "CI_PIPELINE_URL"]),

            # 执行信息
            "executor": "jenkins" if os.getenv("JENKINS_URL") else "local",
            "executor_user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
            "executor_host": socket.gethostname(),
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

            # 敏感信息标记
            "readonly_mode": str(settings.get("api.read_only", False)).lower(),
            "write_enabled": str(settings.get("safety.allow_write_operations", True)).lower(),
        }

        # 过滤掉值为 None 的项
        return {k: v for k, v in env_data.items() if v not in (None, "")}

    # ============================================================
    # 4. 采集执行器信息
    # ============================================================
    @classmethod
    def _collect_executor(cls) -> Dict[str, Any]:
        """采集执行器信息（executor.json 格式）"""
        # 优先识别 Jenkins
        if os.getenv("JENKINS_URL"):
            return {
                "name": "Jenkins",
                "type": "jenkins",
                "url": os.getenv("JENKINS_URL"),
                "buildOrder": _safe_int(os.getenv("BUILD_NUMBER")),
                "buildName": os.getenv("JOB_NAME", "unknown-job"),
                "buildUrl": os.getenv("BUILD_URL"),
                "reportUrl": f"{os.getenv('BUILD_URL', '')}allure",
            }

        # GitLab CI
        if os.getenv("CI_PIPELINE_URL"):
            return {
                "name": "GitLab CI",
                "type": "gitlab",
                "url": os.getenv("CI_PROJECT_URL", ""),
                "buildOrder": _safe_int(os.getenv("CI_PIPELINE_IID")),
                "buildName": os.getenv("CI_PIPELINE_ID", "unknown"),
                "buildUrl": os.getenv("CI_PIPELINE_URL"),
                "reportUrl": "",
            }

        # GitHub Actions
        if os.getenv("GITHUB_RUN_NUMBER"):
            return {
                "name": "GitHub Actions",
                "type": "github",
                "url": f"https://github.com/{os.getenv('GITHUB_REPOSITORY', '')}",
                "buildOrder": _safe_int(os.getenv("GITHUB_RUN_NUMBER")),
                "buildName": os.getenv("GITHUB_WORKFLOW", "unknown"),
                "buildUrl": f"https://github.com/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}",
                "reportUrl": "",
            }

        # 本地执行
        return {
            "name": "Local",
            "type": "local",
            "url": "",
            "buildOrder": int(datetime.now().timestamp()),
            "buildName": "local-run",
            "buildUrl": "",
            "reportUrl": "",
        }


# ============================================================
# 内部辅助函数
# ============================================================
def _get_ci_var(names: list, default: str = "") -> str:
    """按顺序读取 CI 环境变量，返回第一个非空值"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


def _short_commit(commit: str, length: int = 8) -> str:
    """缩短 commit hash"""
    if not commit:
        return ""
    return commit[:length]


def _infer_protocol(url: str) -> str:
    """从 URL 推断协议"""
    if not url:
        return ""
    if url.startswith("https://"):
        return "HTTPS"
    if url.startswith("http://"):
        return "HTTP"
    return ""


def _detect_ci_platform() -> str:
    """检测当前 CI 平台"""
    if os.getenv("JENKINS_URL"):
        return "Jenkins"
    if os.getenv("GITLAB_CI"):
        return "GitLab CI"
    if os.getenv("GITHUB_ACTIONS"):
        return "GitHub Actions"
    if os.getenv("CIRCLECI"):
        return "CircleCI"
    if os.getenv("TRAVIS"):
        return "Travis CI"
    return "Local"


def _safe_int(value: Any) -> int:
    """安全转 int"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _escape_value(value: Any) -> str:
    """
    转义 properties 中的特殊字符

    Java Properties 格式要求：
        \\ → \\\\
        换行 → \\n
        = : # ! → 前面加反斜杠
    """
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("=", "\\=")
    s = s.replace(":", "\\:")
    return s


# ============================================================
# 便捷函数（对外主入口）
# ============================================================
def write_allure_env(results_dir: Optional[str] = None) -> None:
    """
    一键写入 environment.properties 和 executor.json

    :param results_dir: allure-results 目录
    """
    try:
        AllureEnvWriter.write(results_dir)
        AllureEnvWriter.write_executor(results_dir)
    except Exception as e:
        logger.warning(f"[AllureEnv] 环境信息写入失败: {e}", exc_info=True)