# -*- coding: utf-8 -*-
"""
core/reporter/quality_reporter.py
---------------------------------
质量报告生成器（QualityReporter）

职责：
    1. 汇总 CaseResult 列表，统计通过率
    2. 按优先级（P0/P1/P2）统计
    3. 按模块（ecommerce/logistics）统计
    4. 识别新增失败（对比基线）
    5. 生成 quality-report.json

生成的报告结构示例：
{
    "summary": {
        "total": 156, "passed": 153, "failed": 2, "skipped": 1,
        "pass_rate": "98.1%", "pass_rate_value": 98.1,
        "p0_pass_rate": "100%", "p0_pass_rate_value": 100.0,
        "p1_pass_rate": "97.4%", "p1_pass_rate_value": 97.4,
        "duration": 720
    },
    "by_module": {
        "ecommerce": {...},
        "logistics": {...}
    },
    "failed_cases": [...],
    "new_failures": [...],
    "timestamp": "2026-01-05 06:00:00"
}
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from common.utils import FileUtil
from core.models import CaseResult, CaseStatus
from config import get_current_env

logger = logging.getLogger(__name__)


class QualityReporter:
    """
    质量报告生成器（静态方法）
    """

    DEFAULT_OUTPUT = "reports/artifacts/quality-report.json"

    # ============================================================
    # 1. 主入口：生成报告
    # ============================================================
    @classmethod
    def generate(
            cls,
            case_results: List[CaseResult],
            output_path: Optional[str] = None,
            baseline_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成质量报告

        :param case_results:  所有用例的 CaseResult 列表
        :param output_path:   输出路径（默认 reports/artifacts/quality-report.json）
        :param baseline_path: 基线报告路径（用于对比新增失败，如上次成功构建的报告）
        :return:              质量报告字典
        """
        output_path = output_path or cls.DEFAULT_OUTPUT
        FileUtil.ensure_dir(output_path)

        # 1. 基础汇总
        summary = cls._calculate_summary(case_results)

        # 2. 按模块汇总
        by_module = cls._aggregate_by_module(case_results)

        # 3. 按优先级汇总
        by_priority = cls._aggregate_by_priority(case_results)

        # 4. 失败用例列表
        failed_cases = cls._collect_failed_cases(case_results)

        # 5. 新增失败（对比基线）
        new_failures = []
        if baseline_path and FileUtil.exists(baseline_path):
            try:
                baseline = FileUtil.load_json(baseline_path)
                new_failures = cls._detect_new_failures(failed_cases, baseline)
            except Exception as e:
                logger.warning(f"[Quality] 基线对比失败: {e}")

        # 6. 组装报告
        report = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "env": get_current_env(),
            "summary": summary,
            "by_module": by_module,
            "by_priority": by_priority,
            "failed_cases": failed_cases,
            "new_failures": new_failures,
        }

        # 7. 写入文件
        FileUtil.dump_json(report, output_path)
        logger.info(
            f"[Quality] 报告已生成: {output_path} "
            f"(total={summary['total']}, pass={summary['pass_rate']})"
        )

        return report

    # ============================================================
    # 2. 基础汇总
    # ============================================================
    @classmethod
    def _calculate_summary(cls, case_results: List[CaseResult]) -> Dict[str, Any]:
        """计算整体与各优先级的通过率"""
        total = len(case_results)
        if total == 0:
            return {
                "total": 0, "passed": 0, "failed": 0, "skipped": 0, "broken": 0,
                "pass_rate": "0%", "pass_rate_value": 0.0,
                "p0_total": 0, "p0_pass_rate": "0%", "p0_pass_rate_value": 0.0,
                "p1_total": 0, "p1_pass_rate": "0%", "p1_pass_rate_value": 0.0,
                "duration": 0,
            }

        passed = sum(1 for c in case_results if c.status == CaseStatus.PASSED)
        failed = sum(1 for c in case_results if c.status == CaseStatus.FAILED)
        skipped = sum(1 for c in case_results if c.status == CaseStatus.SKIPPED)
        broken = sum(1 for c in case_results if c.status == CaseStatus.BROKEN)

        # 通过率（排除 skipped）
        effective_total = total - skipped
        pass_rate_value = round(passed / effective_total * 100, 2) if effective_total > 0 else 0.0

        # P0 统计
        p0_cases = [c for c in case_results if c.priority.upper() == "P0"]
        p0_total = len(p0_cases)
        p0_passed = sum(1 for c in p0_cases if c.status == CaseStatus.PASSED)
        p0_rate = round(p0_passed / p0_total * 100, 2) if p0_total > 0 else 100.0

        # P1 统计
        p1_cases = [c for c in case_results if c.priority.upper() == "P1"]
        p1_total = len(p1_cases)
        p1_passed = sum(1 for c in p1_cases if c.status == CaseStatus.PASSED)
        p1_rate = round(p1_passed / p1_total * 100, 2) if p1_total > 0 else 100.0

        # 总耗时
        total_duration = round(sum(c.duration for c in case_results), 2)

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "broken": broken,
            "pass_rate": f"{pass_rate_value}%",
            "pass_rate_value": pass_rate_value,
            "p0_total": p0_total,
            "p0_failed": sum(1 for c in p0_cases if c.status != CaseStatus.PASSED),
            "p0_pass_rate": f"{p0_rate}%",
            "p0_pass_rate_value": p0_rate,
            "p1_total": p1_total,
            "p1_failed": sum(1 for c in p1_cases if c.status != CaseStatus.PASSED),
            "p1_pass_rate": f"{p1_rate}%",
            "p1_pass_rate_value": p1_rate,
            "duration": total_duration,
        }

    # ============================================================
    # 3. 按模块汇总
    # ============================================================
    @classmethod
    def _aggregate_by_module(cls, case_results: List[CaseResult]) -> Dict[str, Any]:
        """按模块（ecommerce/logistics）统计"""
        modules: Dict[str, List[CaseResult]] = {}
        for case in case_results:
            modules.setdefault(case.module or "unknown", []).append(case)

        result = {}
        for module, cases in modules.items():
            total = len(cases)
            passed = sum(1 for c in cases if c.status == CaseStatus.PASSED)
            failed = sum(1 for c in cases if c.status == CaseStatus.FAILED)
            pass_rate = round(passed / total * 100, 2) if total > 0 else 0.0
            result[module] = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": f"{pass_rate}%",
                "pass_rate_value": pass_rate,
                "duration": round(sum(c.duration for c in cases), 2),
            }
        return result

    # ============================================================
    # 4. 按优先级汇总
    # ============================================================
    @classmethod
    def _aggregate_by_priority(cls, case_results: List[CaseResult]) -> Dict[str, Any]:
        """按 P0 / P1 / P2 / P3 统计"""
        priorities: Dict[str, List[CaseResult]] = {}
        for case in case_results:
            p = (case.priority or "P2").upper()
            priorities.setdefault(p, []).append(case)

        result = {}
        for p, cases in priorities.items():
            total = len(cases)
            passed = sum(1 for c in cases if c.status == CaseStatus.PASSED)
            pass_rate = round(passed / total * 100, 2) if total > 0 else 0.0
            result[p] = {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": f"{pass_rate}%",
            }
        return result

    # ============================================================
    # 5. 失败用例清单
    # ============================================================
    @classmethod
    def _collect_failed_cases(cls, case_results: List[CaseResult]) -> List[Dict[str, Any]]:
        """收集失败用例（含具体错误信息）"""
        failed = []
        for case in case_results:
            if case.status == CaseStatus.FAILED:
                failed.append({
                    "name": case.case_name,
                    "module": case.module,
                    "priority": case.priority,
                    "error": case.error_message or "未知错误",
                    "trace_id": case.trace_id,
                    "duration": case.duration,
                })
        return failed

    # ============================================================
    # 6. 新增失败检测（对比基线）
    # ============================================================
    @classmethod
    def _detect_new_failures(
            cls,
            current_failed: List[Dict[str, Any]],
            baseline: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        检测新增失败用例

        :param current_failed: 本次失败用例列表
        :param baseline:       基线报告（含上次失败用例列表）
        :return:               新增失败（本次失败 - 上次失败）
        """
        baseline_failed = baseline.get("failed_cases", [])
        baseline_names = {item.get("name") for item in baseline_failed if item.get("name")}

        new_failures = []
        for item in current_failed:
            if item.get("name") not in baseline_names:
                new_failures.append(item)

        return new_failures

    # ============================================================
    # 7. 便捷方法：读取已有报告
    # ============================================================
    @classmethod
    def load(cls, path: str) -> Optional[Dict[str, Any]]:
        """加载已生成的质量报告"""
        if not FileUtil.exists(path):
            return None
        try:
            return FileUtil.load_json(path)
        except Exception as e:
            logger.warning(f"[Quality] 加载报告失败: {e}")
            return None