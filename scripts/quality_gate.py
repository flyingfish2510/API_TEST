# -*- coding: utf-8 -*-
"""
scripts/quality_gate.py
-----------------------
质量门禁判定脚本

功能：
    1. 读取测试结果（quality-report.json 或 allure-results）
    2. 加载质量门禁规则（ci/quality-gate-rules.json）
    3. 判定整体通过率、P0 用例通过率、执行耗时、新增失败等指标
    4. 支持四种模式：mr / daily / release / inspection
    5. 输出门禁结论和退出码（阻断或放行）

用法：
    # MR 门禁（极严）
    python scripts/quality_gate.py --mode=mr

    # 每日回归（容忍存量失败）
    python scripts/quality_gate.py --mode=daily --baseline=reports/artifacts/quality-report-prev.json

    # 发版门禁（双重校验）
    python scripts/quality_gate.py --mode=release --strict
"""
import os
import sys
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("quality_gate")

# ============================================================
# 门禁规则默认值
# ============================================================
DEFAULT_RULES = {
    "mr": {
        "pass_rate": 100.0,
        "p0_pass_rate": 100.0,
        "max_new_failures": 0,
        "max_duration_seconds": 300,
        "allow_unstable": False,
    },
    "daily": {
        "pass_rate": 95.0,
        "p0_pass_rate": 100.0,
        "max_new_failures": 5,
        "max_duration_seconds": 1800,
        "allow_unstable": True,
    },
    "release": {
        "pass_rate": 98.0,
        "p0_pass_rate": 100.0,
        "max_new_failures": 0,
        "max_duration_seconds": 2700,
        "allow_unstable": False,
    },
    "inspection": {
        "pass_rate": 90.0,
        "p0_pass_rate": 100.0,
        "max_new_failures": 0,
        "max_duration_seconds": 120,
        "allow_unstable": True,
    },
}


# ============================================================
# 门禁判定器
# ============================================================
class QualityGate:
    """质量门禁判定器"""

    def __init__(
            self,
            mode: str,
            quality_report_path: str,
            rules_path: Optional[str] = None,
            baseline_path: Optional[str] = None,
            strict: bool = False,
    ):
        self.mode = mode.lower()
        self.quality_report_path = Path(quality_report_path)
        self.rules_path = Path(rules_path) if rules_path else None
        self.baseline_path = Path(baseline_path) if baseline_path else None
        self.strict = strict

        self.report = self._load_json(self.quality_report_path, "质量报告")
        self.baseline = self._load_json(self.baseline_path, "基线报告", required=False)
        self.rules = self._load_rules()

    def _load_json(self, path: Path, name: str, required: bool = True) -> dict:
        """加载 JSON 文件"""
        if not path or not path.exists():
            if required:
                logger.error(f"{name} 文件不存在: {path}")
                sys.exit(2)
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"{name} 解析失败: {e}")
            if required:
                sys.exit(2)
            return {}

    def _load_rules(self) -> dict:
        """加载门禁规则"""
        if self.rules_path and self.rules_path.exists():
            try:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    all_rules = json.load(f)
                # 兼容两种格式：{mode: {...}} 或 {"modes": {mode: {...}}}
                if "modes" in all_rules:
                    all_rules = all_rules["modes"]
                return all_rules.get(self.mode, DEFAULT_RULES.get(self.mode, {}))
            except Exception as e:
                logger.warning(f"加载规则文件失败: {e}，使用默认规则")

        return DEFAULT_RULES.get(self.mode, DEFAULT_RULES["daily"])

    # ============================================================
    # 核心判定逻辑
    # ============================================================
    def evaluate(self) -> dict:
        """执行门禁判定"""
        summary = self.report.get("summary", {})

        # 提取关键指标
        pass_rate = summary.get("pass_rate_value", 0.0)
        p0_pass_rate = summary.get("p0_pass_rate_value", 100.0)
        duration = summary.get("duration", 0)

        # 新增失败
        new_failures = len(self.report.get("new_failures", []))
        if self.baseline:
            new_failures = self._calc_new_failures()

        # 检查项
        checks = []

        # 1. 整体通过率
        if pass_rate < self.rules.get("pass_rate", 95.0):
            checks.append({
                "name": "整体通过率",
                "passed": False,
                "actual": f"{pass_rate}%",
                "expected": f"≥ {self.rules['pass_rate']}%",
            })
        else:
            checks.append({
                "name": "整体通过率",
                "passed": True,
                "actual": f"{pass_rate}%",
                "expected": f"≥ {self.rules['pass_rate']}%",
            })

        # 2. P0 通过率
        if p0_pass_rate < self.rules.get("p0_pass_rate", 100.0):
            checks.append({
                "name": "P0 通过率",
                "passed": False,
                "actual": f"{p0_pass_rate}%",
                "expected": f"≥ {self.rules['p0_pass_rate']}%",
            })
        else:
            checks.append({
                "name": "P0 通过率",
                "passed": True,
                "actual": f"{p0_pass_rate}%",
                "expected": f"≥ {self.rules['p0_pass_rate']}%",
            })

        # 3. 新增失败
        if new_failures > self.rules.get("max_new_failures", 0):
            checks.append({
                "name": "新增失败",
                "passed": False,
                "actual": new_failures,
                "expected": f"≤ {self.rules['max_new_failures']}",
            })
        else:
            checks.append({
                "name": "新增失败",
                "passed": True,
                "actual": new_failures,
                "expected": f"≤ {self.rules['max_new_failures']}",
            })

        # 4. 执行耗时
        if duration > self.rules.get("max_duration_seconds", 1800):
            checks.append({
                "name": "执行耗时",
                "passed": False,
                "actual": f"{duration}s",
                "expected": f"≤ {self.rules['max_duration_seconds']}s",
            })
        else:
            checks.append({
                "name": "执行耗时",
                "passed": True,
                "actual": f"{duration}s",
                "expected": f"≤ {self.rules['max_duration_seconds']}s",
            })

        # 汇总
        all_passed = all(c["passed"] for c in checks)
        if self.strict and any(not c["passed"] for c in checks):
            all_passed = False

        result = {
            "mode": self.mode,
            "passed": all_passed,
            "checks": checks,
            "summary": summary,
            "new_failures": new_failures,
        }

        self._print_result(result)
        return result

    def _calc_new_failures(self) -> int:
        """对比基线计算新增失败"""
        current_failed = self.report.get("failed_cases", [])
        baseline_failed = self.baseline.get("failed_cases", [])

        if not baseline_failed:
            return len(current_failed)

        baseline_names = {c.get("name") for c in baseline_failed if c.get("name")}
        new_failures = [c for c in current_failed if c.get("name") not in baseline_names]
        return len(new_failures)

    def _print_result(self, result: dict):
        """格式化输出判定结果"""
        print()
        print("=" * 70)
        print(f"  质量门禁判定结果（模式: {result['mode'].upper()}）")
        print("=" * 70)

        for check in result["checks"]:
            icon = "✅" if check["passed"] else "❌"
            print(f"  {icon} {check['name']}: 实际 {check['actual']}，要求 {check['expected']}")

        print("-" * 70)
        if result["passed"]:
            print("  🎉 门禁通过，允许继续后续流程")
        else:
            print("  🛑 门禁未通过，阻断后续流程")
        print("=" * 70)
        print()


# ============================================================
# CLI 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="API_TEST 质量门禁判定工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    python scripts/quality_gate.py --mode=mr
    python scripts/quality_gate.py --mode=daily --baseline=prev-report.json
    python scripts/quality_gate.py --mode=release --strict
        """,
    )
    parser.add_argument("--mode", required=True,
                        choices=["mr", "daily", "release", "inspection"],
                        help="门禁模式（mr=MR门禁 / daily=每日回归 / release=发版 / inspection=巡检）")
    parser.add_argument("--quality-report",
                        default=str(PROJECT_ROOT / "reports" / "artifacts" / "quality-report.json"),
                        help="质量报告路径")
    parser.add_argument("--baseline",
                        help="基线报告路径（用于检测新增失败）")
    parser.add_argument("--rules",
                        default=str(PROJECT_ROOT / "ci" / "quality-gate-rules.json"),
                        help="门禁规则文件路径")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式（任何未通过都阻断）")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 执行判定
    gate = QualityGate(
        mode=args.mode,
        quality_report_path=args.quality_report,
        rules_path=args.rules,
        baseline_path=args.baseline,
        strict=args.strict,
    )
    result = gate.evaluate()

    # 输出 JSON（供 Jenkins 归档）
    output_path = Path(args.quality_report).parent / "gate-result.json"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # 退出码
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()