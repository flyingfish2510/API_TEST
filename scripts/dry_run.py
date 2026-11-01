# -*- coding: utf-8 -*-
"""
scripts/dry_run.py
------------------
YAML 用例语法与结构预检脚本

功能：
    1. 检查所有 YAML 用例的语法正确性
    2. 校验 YAML 结构（config / setup / test / teardown）
    3. 检查 required 字段（name / request / url / method）
    4. 校验断言类型（eq / contains / schema / db_eq 等）是否合法
    5. 校验变量占位符（{{xxx}}）格式
    6. 检查引用的 include 片段是否存在
    7. 检查 schema / mock 文件是否存在

用法：
    # 预检全部用例
    python scripts/dry_run.py

    # 预检指定模块
    python scripts/dry_run.py --module=ecommerce

    # 只检查 YAML 语法（不检查引用文件）
    python scripts/dry_run.py --syntax-only

    # 严格模式（任何警告都视为失败）
    python scripts/dry_run.py --strict

    # 输出 JSON 报告
    python scripts/dry_run.py --output=reports/artifacts/dry-run.json
"""
import os
import re
import sys
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict

import yaml

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dry_run")

# ============================================================
# 常量
# ============================================================
# 合法断言类型（与 AssertFactory._routes 保持一致）
VALID_ASSERT_TYPES = {
    "status_code", "eq", "contains", "exists", "not_contains", "header",
    "regex", "type", "not_null", "in", "length",
    "schema",
    "db_eq", "db_exists", "db_count", "db_row",
    "snapshot",
    "max_time", "min_time", "p95_time", "avg_time", "percentile",
}

# 合法 HTTP 方法
VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

# 合法步骤类型
VALID_STEP_KEYS = {"config", "setup", "test", "teardown"}

# 合法 extract 源
VALID_EXTRACT_SOURCES = {"json", "regex", "header", "cookie"}

# 占位符正则（含函数调用）
VAR_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")

# 变量命名空间
VALID_NAMESPACES = {
    "env", "context", "faker", "time", "crypto", "random", "jsonpath",
    "file", "snapshot", "uuid", "timestamp", "timestamp_ms", "today",
}


# ============================================================
# 数据模型
# ============================================================
@dataclass
class Issue:
    """单个问题"""
    level: str  # ERROR / WARNING / INFO
    file: str
    message: str
    line: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    """检查结果"""
    file: str
    passed: bool = True
    issues: List[Issue] = field(default_factory=list)

    def add_issue(self, level: str, message: str):
        self.issues.append(Issue(level=level, file=self.file, message=message))
        if level == "ERROR":
            self.passed = False

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "WARNING")


# ============================================================
# YAML 预检器
# ============================================================
class DryRunChecker:
    """YAML 用例预检器"""

    def __init__(
            self,
            testcases_root: str = "testcases",
            strict: bool = False,
            syntax_only: bool = False,
    ):
        self.testcases_root = Path(testcases_root).resolve()
        self.strict = strict
        self.syntax_only = syntax_only
        self.results: List[CheckResult] = []

    # ============================================================
    # 主入口
    # ============================================================
    def run(self, module: Optional[str] = None) -> dict:
        """执行全量预检"""
        yaml_files = self._discover(module)
        logger.info(f"发现 {len(yaml_files)} 个 YAML 文件")

        for yaml_file in yaml_files:
            result = self._check_file(yaml_file)
            self.results.append(result)

        return self._build_report()

    # ============================================================
    # 文件发现
    # ============================================================
    def _discover(self, module: Optional[str]) -> List[Path]:
        """发现所有 YAML 文件"""
        if not self.testcases_root.exists():
            logger.error(f"用例根目录不存在: {self.testcases_root}")
            return []

        search_dir = self.testcases_root
        if module:
            search_dir = self.testcases_root / module
            if not search_dir.exists():
                logger.error(f"模块目录不存在: {search_dir}")
                return []

        files = list(search_dir.rglob("*.yaml")) + list(search_dir.rglob("*.yml"))
        return sorted(files)

    # ============================================================
    # 单文件检查
    # ============================================================
    def _check_file(self, path: Path) -> CheckResult:
        """检查单个 YAML 文件"""
        rel_path = str(path.relative_to(PROJECT_ROOT))
        result = CheckResult(file=rel_path)

        # 1. YAML 语法检查
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            result.add_issue("ERROR", f"YAML 语法错误: {e}")
            return result
        except Exception as e:
            result.add_issue("ERROR", f"文件读取失败: {e}")
            return result

        if data is None:
            result.add_issue("ERROR", "YAML 文件为空")
            return result

        if self.syntax_only:
            # 只检查语法，直接返回
            return result

        # 2. 结构检查
        self._check_structure(data, result)

        return result

    # ============================================================
    # 结构检查
    # ============================================================
    def _check_structure(self, data: any, result: CheckResult):
        """检查 YAML 顶层结构"""
        if not isinstance(data, list):
            result.add_issue(
                "ERROR",
                f"根节点必须是列表，实际是 {type(data).__name__}"
            )
            return

        has_test = False
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                result.add_issue(
                    "ERROR",
                    f"第 {idx + 1} 项必须是字典，实际是 {type(item).__name__}"
                )
                continue

            keys = set(item.keys())
            invalid = keys - VALID_STEP_KEYS
            if invalid:
                result.add_issue(
                    "ERROR",
                    f"第 {idx + 1} 项有非法主键: {invalid}"
                )

            # config 检查
            if "config" in item:
                self._check_config(item["config"], result, idx)

            # setup / teardown 检查
            if "setup" in item:
                self._check_steps(item["setup"], "setup", result, idx)
            if "teardown" in item:
                self._check_steps(item["teardown"], "teardown", result, idx)

            # test 检查
            if "test" in item:
                has_test = True
                self._check_test(item["test"], result, idx)

        if not has_test:
            result.add_issue("ERROR", "YAML 用例必须至少包含一个 'test' 步骤")

    # ============================================================
    # config 检查
    # ============================================================
    def _check_config(self, config: any, result: CheckResult, idx: int):
        """检查 config 节点"""
        if not isinstance(config, dict):
            result.add_issue("ERROR", f"config 必须是字典，第 {idx + 1} 项")
            return

        if "name" not in config:
            result.add_issue("WARNING", f"config 缺少 name 字段，第 {idx + 1} 项")

        # tags 必须是列表
        tags = config.get("tags")
        if tags is not None and not isinstance(tags, list):
            result.add_issue("ERROR", "config.tags 必须是列表")

        # priority 必须是 P0/P1/P2/P3
        priority = config.get("priority")
        if priority is not None and priority not in {"P0", "P1", "P2", "P3"}:
            result.add_issue(
                "WARNING",
                f"config.priority 建议使用 P0/P1/P2/P3，实际: {priority}"
            )

    # ============================================================
    # setup / teardown 检查
    # ============================================================
    def _check_steps(self, steps: any, phase: str, result: CheckResult, idx: int):
        """检查 setup / teardown 步骤列表"""
        if not isinstance(steps, list):
            result.add_issue("ERROR", f"{phase} 必须是列表，第 {idx + 1} 项")
            return

        for sub_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                result.add_issue(
                    "ERROR",
                    f"{phase}[{sub_idx}] 必须是字典"
                )
                continue

            # 支持 include
            if "include" in step:
                self._check_include(step["include"], result, phase, sub_idx)
                continue

            # 支持 mock
            if "mock" in step:
                self._check_mock(step["mock"], result, phase, sub_idx)
                continue

            # 支持 test
            if "test" in step:
                self._check_test(step["test"], result, sub_idx)
                continue

            result.add_issue(
                "ERROR",
                f"{phase}[{sub_idx}] 缺少 test / include / mock 节点"
            )

    # ============================================================
    # include 片段检查
    # ============================================================
    def _check_include(self, include_path: str, result: CheckResult, phase: str, idx: int):
        """检查 include 片段是否存在"""
        if not isinstance(include_path, str):
            result.add_issue("ERROR", f"{phase}[{idx}] include 必须是字符串")
            return

        # 搜索路径：data/fragments/ + 当前目录相对路径
        candidates = [
            PROJECT_ROOT / "data" / "fragments" / include_path,
            PROJECT_ROOT / include_path,
        ]

        if not any(c.exists() for c in candidates):
            result.add_issue(
                "ERROR",
                f"{phase}[{idx}] include 片段不存在: {include_path}"
            )

    # ============================================================
    # mock 检查
    # ============================================================
    def _check_mock(self, mock_config: any, result: CheckResult, phase: str, idx: int):
        """检查 mock 规则文件"""
        if not isinstance(mock_config, dict):
            result.add_issue("ERROR", f"{phase}[{idx}] mock 必须是字典")
            return

        rule = mock_config.get("rule")
        if not rule:
            result.add_issue("ERROR", f"{phase}[{idx}] mock 缺少 rule 字段")
            return

        rule_path = PROJECT_ROOT / "data" / "mocks" / rule
        if not rule_path.exists():
            result.add_issue(
                "ERROR",
                f"{phase}[{idx}] mock 规则文件不存在: data/mocks/{rule}"
            )

    # ============================================================
    # test 步骤检查
    # ============================================================
    def _check_test(self, test: any, result: CheckResult, idx: int):
        """检查 test 节点"""
        if not isinstance(test, dict):
            result.add_issue("ERROR", f"test[{idx}] 必须是字典")
            return

        # 1. name 检查
        if "name" not in test:
            result.add_issue("WARNING", f"test[{idx}] 缺少 name 字段")

        # 2. request 检查
        request = test.get("request")
        if not request:
            result.add_issue("ERROR", f"test[{idx}] 缺少 request 字段")
            return

        self._check_request(request, result, idx)

        # 3. extract 检查
        extract = test.get("extract")
        if extract:
            self._check_extract(extract, result, idx)

        # 4. validate 检查
        validate = test.get("validate")
        if validate:
            self._check_validate(validate, result, idx)

    def _check_request(self, request: any, result: CheckResult, idx: int):
        """检查 request 节点"""
        if not isinstance(request, dict):
            result.add_issue("ERROR", f"test[{idx}].request 必须是字典")
            return

        # URL 检查
        url = request.get("url")
        if not url:
            result.add_issue("ERROR", f"test[{idx}].request 缺少 url")
        elif not isinstance(url, str):
            result.add_issue("ERROR", f"test[{idx}].request.url 必须是字符串")
        else:
            # 检查占位符
            self._check_variables(url, result, f"test[{idx}].request.url")

        # Method 检查
        method = request.get("method", "GET").upper()
        if method not in VALID_METHODS:
            result.add_issue(
                "ERROR",
                f"test[{idx}].request.method 非法: {method}"
            )

        # 请求体互斥检查
        has_json = request.get("json") is not None
        has_data = request.get("data") is not None
        has_files = request.get("files") is not None

        if has_json and (has_data or has_files):
            result.add_issue(
                "ERROR",
                f"test[{idx}].request 中 json 不能与 data/files 共存"
            )

        # 检查请求体中的占位符
        if has_json:
            self._check_variables(request["json"], result, f"test[{idx}].request.json")
        if has_data:
            self._check_variables(request["data"], result, f"test[{idx}].request.data")

    def _check_extract(self, extract: any, result: CheckResult, idx: int):
        """检查 extract 节点"""
        if not isinstance(extract, dict):
            result.add_issue("ERROR", f"test[{idx}].extract 必须是字典")
            return

        for var_name, rule in extract.items():
            if not isinstance(var_name, str):
                result.add_issue("ERROR", f"test[{idx}].extract 变量名必须是字符串")
                continue

            if isinstance(rule, str):
                # 简写：JSONPath
                if not rule.startswith("$"):
                    result.add_issue(
                        "WARNING",
                        f"test[{idx}].extract[{var_name}] 建议以 $ 开头的 JSONPath"
                    )
            elif isinstance(rule, dict):
                source = rule.get("source", "json")
                if source not in VALID_EXTRACT_SOURCES:
                    result.add_issue(
                        "ERROR",
                        f"test[{idx}].extract[{var_name}].source 非法: {source}"
                    )
                # 按 source 校验必填字段
                if source == "json" and not rule.get("path"):
                    result.add_issue(
                        "ERROR",
                        f"test[{idx}].extract[{var_name}] source=json 时缺少 path"
                    )
                elif source == "regex" and not rule.get("pattern"):
                    result.add_issue(
                        "ERROR",
                        f"test[{idx}].extract[{var_name}] source=regex 时缺少 pattern"
                    )
                elif source in ("header", "cookie") and not rule.get("name"):
                    result.add_issue(
                        "ERROR",
                        f"test[{idx}].extract[{var_name}] source={source} 时缺少 name"
                    )
            else:
                result.add_issue(
                    "ERROR",
                    f"test[{idx}].extract[{var_name}] 必须是字符串或字典"
                )

    def _check_validate(self, validate: any, result: CheckResult, idx: int):
        """检查 validate 节点"""
        if not isinstance(validate, list):
            result.add_issue("ERROR", f"test[{idx}].validate 必须是列表")
            return

        for v_idx, validator in enumerate(validate):
            if not isinstance(validator, dict):
                result.add_issue(
                    "ERROR",
                    f"test[{idx}].validate[{v_idx}] 必须是字典"
                )
                continue

            if len(validator) != 1:
                result.add_issue(
                    "ERROR",
                    f"test[{idx}].validate[{v_idx}] 必须是单键字典（如 {{eq: [...]}}）"
                )
                continue

            assert_type = next(iter(validator.keys()))
            if assert_type not in VALID_ASSERT_TYPES:
                result.add_issue(
                    "ERROR",
                    f"test[{idx}].validate[{v_idx}] 未知断言类型: {assert_type}"
                )

            # schema 检查
            if assert_type == "schema":
                args = validator["schema"]
                if isinstance(args, str):
                    schema_path = PROJECT_ROOT / "data" / "schemas" / args
                    if not schema_path.exists():
                        result.add_issue(
                            "ERROR",
                            f"test[{idx}].validate[{v_idx}] Schema 文件不存在: {args}"
                        )

            # 检查值中的占位符
            args = validator[assert_type]
            self._check_variables(args, result, f"test[{idx}].validate[{v_idx}].{assert_type}")

    # ============================================================
    # 变量占位符检查
    # ============================================================
    def _check_variables(self, data: any, result: CheckResult, context: str):
        """递归检查 {{xxx}} 占位符"""
        if isinstance(data, str):
            for match in VAR_PATTERN.finditer(data):
                expr = match.group(1).strip()
                self._check_single_variable(expr, result, context)
        elif isinstance(data, dict):
            for v in data.values():
                self._check_variables(v, result, context)
        elif isinstance(data, list):
            for v in data:
                self._check_variables(v, result, context)

    def _check_single_variable(self, expr: str, result: CheckResult, context: str):
        """检查单个占位符"""
        # 空占位符
        if not expr:
            result.add_issue("ERROR", f"{context} 存在空占位符 {{{{}}}}")
            return

        # 提取命名空间
        if "." in expr:
            ns = expr.split(".")[0]
            if ns not in VALID_NAMESPACES:
                result.add_issue(
                    "WARNING",
                    f"{context} 未知命名空间: {ns}（{expr}）"
                )
        else:
            # 无命名空间：可能是内置函数或上下文变量
            base = expr.split("(")[0].strip()
            if base not in {"uuid", "timestamp", "timestamp_ms", "today"}:
                # 视为上下文变量，不报错（Context 中可能有）
                pass

    # ============================================================
    # 报告生成
    # ============================================================
    def _build_report(self) -> dict:
        """生成汇总报告"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        total_errors = sum(r.error_count for r in self.results)
        total_warnings = sum(r.warning_count for r in self.results)

        report = {
            "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_files": total,
                "passed_files": passed,
                "failed_files": failed,
                "total_errors": total_errors,
                "total_warnings": total_warnings,
            },
            "results": [r.to_dict() for r in self.results],
        }
        return report


# ============================================================
# 输出
# ============================================================
def print_report(report: dict, strict: bool = False):
    """格式化输出报告"""
    summary = report["summary"]

    print()
    print("=" * 70)
    print("  YAML 预检结果")
    print("=" * 70)
    print(f"  总文件数: {summary['total_files']}")
    print(f"  通过:     {summary['passed_files']} ✅")
    print(f"  失败:     {summary['failed_files']} ❌")
    print(f"  错误数:   {summary['total_errors']}")
    print(f"  警告数:   {summary['total_warnings']}")
    print("=" * 70)

    # 输出问题详情
    for result in report["results"]:
        if not result["issues"]:
            continue

        print(f"\n📄 {result['file']}")
        for issue in result["issues"]:
            icon = {"ERROR": "❌", "WARNING": "⚠️ ", "INFO": "ℹ️ "}.get(issue["level"], "•")
            print(f"  {icon} [{issue['level']}] {issue['message']}")

    print()


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="API_TEST YAML 用例预检工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 预检全部用例
    python scripts/dry_run.py

    # 只预检电商模块
    python scripts/dry_run.py --module=ecommerce

    # 只检查 YAML 语法
    python scripts/dry_run.py --syntax-only

    # 严格模式（任何警告都视为失败）
    python scripts/dry_run.py --strict

    # 输出 JSON 报告
    python scripts/dry_run.py --output=reports/artifacts/dry-run.json
        """,
    )
    parser.add_argument("--module", help="只检查指定模块")
    parser.add_argument("--testcases-root", default="testcases", help="用例根目录")
    parser.add_argument("--syntax-only", action="store_true", help="只检查语法")
    parser.add_argument("--strict", action="store_true", help="严格模式：警告也视为失败")
    parser.add_argument("--output", help="输出 JSON 报告路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 执行检查
    checker = DryRunChecker(
        testcases_root=args.testcases_root,
        strict=args.strict,
        syntax_only=args.syntax_only,
    )

    report = checker.run(module=args.module)

    # 输出
    print_report(report, strict=args.strict)

    # JSON 输出
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"📋 报告已写入: {output_path}")

    # 退出码
    summary = report["summary"]
    if summary["failed_files"] > 0:
        sys.exit(1)
    if args.strict and summary["total_warnings"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()