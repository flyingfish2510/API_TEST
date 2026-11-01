# -*- coding: utf-8 -*-
"""
scripts/generate_mock.py
------------------------
Mock 规则自动生成脚本

功能：
    1. 从 Swagger/OpenAPI 2.0/3.0 文档生成 Mock 规则骨架
    2. 根据 Schema 自动推断响应字段类型（string/int/boolean/array）
    3. 自动生成动态变量占位符（如 {{$uuid}} / {{$timestamp}}）
    4. 支持过滤（只生成指定 tag/路径的接口）
    5. 输出符合 MockLoader 规范的 JSON 文件

用法：
    # 生成所有接口
    python scripts/generate_mock.py --swagger=api.json --output=payment_gateway.json

    # 只生成指定 tag
    python scripts/generate_mock.py --swagger=api.json --tag=payment --output=payment.json

    # 只生成匹配路径的接口
    python scripts/generate_mock.py --swagger=api.json --path-filter="/pay/*" --output=pay.json
"""
import os
import re
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
logger = logging.getLogger("generate_mock")

# ============================================================
# 类型映射（JSON Schema → Mock 值）
# ============================================================
TYPE_MAP = {
    "string": "sample_string",
    "integer": 0,
    "number": 0.0,
    "boolean": True,
    "array": [],
    "object": {},
    "null": None,
}

# 字段名 → 智能 Mock 值（按字段名猜内容）
FIELD_PATTERN_MAP = {
    r"uuid|guid": "{{$uuid}}",
    r"timestamp|time_stamp": "{{$timestamp}}",
    r"create_time|update_time|ctime|mtime": "{{$timestamp}}",
    r"order_?no|order_?number|out_trade_no": "DD{{$uuid}}",
    r"transaction_?id|trade_?no": "wx_{{$uuid}}",
    r"phone|mobile|tel": "13800138000",
    r"email|mail": "test@example.com",
    r"name|user_name|username": "测试用户",
    r"amount|price|fee|total": 100.00,
    r"status|state": "SUCCESS",
    r"token|access_token": "{{$uuid}}",
    r"id|user_id|uid": 10001,
    r"url|link": "https://example.com",
    r"^code$|return_code|result_code": "SUCCESS",
    r"msg|message|desc": "操作成功",
}


# ============================================================
# Mock 规则生成器
# ============================================================
class MockGenerator:
    """Mock 规则生成器"""

    def __init__(
            self,
            swagger_path: str,
            tag_filter: Optional[str] = None,
            path_filter: Optional[str] = None,
    ):
        self.swagger_path = Path(swagger_path)
        self.tag_filter = tag_filter
        self.path_filter = path_filter
        self.swagger = self._load_swagger()

    def _load_swagger(self) -> dict:
        """加载 Swagger/OpenAPI 文档"""
        if not self.swagger_path.exists():
            raise FileNotFoundError(f"Swagger 文件不存在: {self.swagger_path}")

        with open(self.swagger_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 判断 OpenAPI 版本
        version = data.get("openapi") or data.get("swagger")
        if not version:
            raise ValueError("非法的 Swagger/OpenAPI 文件（缺少 openapi/swagger 字段）")

        logger.info(f"Swagger 版本: {version}")
        return data

    # ============================================================
    # 生成规则
    # ============================================================
    def generate(self, output_name: str) -> dict:
        """生成 Mock 规则 JSON"""
        paths = self.swagger.get("paths", {})

        rules = []
        for path, methods in paths.items():
            # 路径过滤
            if self.path_filter and not self._match_path_filter(path):
                continue

            for method, spec in methods.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue

                # tag 过滤
                tags = spec.get("tags", [])
                if self.tag_filter and self.tag_filter not in tags:
                    continue

                # 生成规则
                rule = self._build_rule(path, method, spec)
                if rule:
                    rules.append(rule)

        result = {
            "name": output_name,
            "description": f"自动生成的 Mock 规则（来源: {self.swagger_path.name}）",
            "version": "1.0.0",
            "rules": rules,
        }

        logger.info(f"生成 {len(rules)} 条规则")
        return result

    # ============================================================
    # 构建单条规则
    # ============================================================
    def _build_rule(self, path: str, method: str, spec: dict) -> Optional[dict]:
        """根据接口定义构建单条 Mock 规则"""
        # 1. 获取响应 Schema（优先 200）
        responses = spec.get("responses", {})
        success_resp = responses.get("200") or responses.get("201") or {}

        # 2. 从 Schema 生成示例响应体
        schema = self._extract_schema(success_resp)
        if schema is None:
            logger.warning(f"跳过无 Schema 接口: {method.upper()} {path}")
            return None

        body = self._generate_from_schema(schema)

        # 3. 根据路径推断业务字段（如订单号、交易号）
        body = self._enrich_body_by_path(path, body)

        # 4. 构建规则
        return {
            "match": {
                "method": method.upper(),
                "path": path,
            },
            "response": {
                "status": 200,
                "delay": 0.1,
                "headers": {
                    "Content-Type": "application/json; charset=utf-8",
                },
                "body": body,
            },
        }

    def _extract_schema(self, response: dict) -> Optional[dict]:
        """从响应中提取 Schema（兼容 OAS 2.0 / 3.0）"""
        if not response:
            return None

        # OpenAPI 3.0: content.application/json.schema
        content = response.get("content", {})
        if "application/json" in content:
            return content["application/json"].get("schema")

        # OpenAPI 2.0: schema
        if "schema" in response:
            return response["schema"]

        return None

    # ============================================================
    # 从 Schema 生成 Mock 数据
    # ============================================================
    def _generate_from_schema(self, schema: dict, depth: int = 0) -> Any:
        """递归生成符合 Schema 的 Mock 数据"""
        if depth > 5:
            # 深度限制，防止循环引用
            return None

        if not schema:
            return None

        # 处理 $ref
        if "$ref" in schema:
            schema = self._resolve_ref(schema["$ref"])
            if schema is None:
                return None

        # 处理 enum
        if "enum" in schema:
            return schema["enum"][0]

        # 处理 example
        if "example" in schema:
            return schema["example"]

        # 处理 oneOf / anyOf
        if "oneOf" in schema and schema["oneOf"]:
            return self._generate_from_schema(schema["oneOf"][0], depth + 1)
        if "anyOf" in schema and schema["anyOf"]:
            return self._generate_from_schema(schema["anyOf"][0], depth + 1)

        # 处理 allOf（合并所有子 Schema）
        if "allOf" in schema:
            merged = {"type": "object", "properties": {}, "required": []}
            for sub in schema["allOf"]:
                sub_schema = self._resolve_ref(sub["$ref"]) if "$ref" in sub else sub
                if sub_schema:
                    merged["properties"].update(sub_schema.get("properties", {}))
                    merged["required"].extend(sub_schema.get("required", []))
            return self._generate_from_schema(merged, depth + 1)

        schema_type = schema.get("type", "object")

        # 基础类型
        if schema_type == "object":
            return self._generate_object(schema, depth)
        elif schema_type == "array":
            items = schema.get("items", {})
            # 数组只生成 1 个元素
            return [self._generate_from_schema(items, depth + 1)]
        elif schema_type in ("string", "integer", "number", "boolean", "null"):
            return self._generate_value(schema)
        else:
            return None

    def _generate_object(self, schema: dict, depth: int) -> dict:
        """生成对象"""
        properties = schema.get("properties", {})
        result = {}

        for prop_name, prop_schema in properties.items():
            # 根据字段名智能生成
            if prop_name.lower() in {p.lower() for p in FIELD_PATTERN_MAP.keys()}:
                result[prop_name] = self._smart_value(prop_name)
            elif prop_schema.get("type") == "object" or "$ref" in prop_schema:
                result[prop_name] = self._generate_from_schema(prop_schema, depth + 1)
            elif prop_schema.get("type") == "array":
                result[prop_name] = self._generate_from_schema(prop_schema, depth + 1)
            else:
                result[prop_name] = self._generate_value(prop_schema)

        return result

    def _generate_value(self, schema: dict) -> Any:
        """生成基础类型的值"""
        schema_type = schema.get("type", "string")

        if "enum" in schema:
            return schema["enum"][0]

        if "example" in schema:
            return schema["example"]

        if "default" in schema:
            return schema["default"]

        if schema_type == "string":
            return "sample_string"
        elif schema_type == "integer":
            return 0
        elif schema_type == "number":
            return 0.0
        elif schema_type == "boolean":
            return True

        return None

    def _smart_value(self, field_name: str) -> Any:
        """根据字段名智能生成值"""
        lower = field_name.lower()
        for pattern, value in FIELD_PATTERN_MAP.items():
            if re.search(pattern, lower):
                return value
        return "sample_value"

    # ============================================================
    # 辅助：$ref 解析
    # ============================================================
    def _resolve_ref(self, ref: str) -> Optional[dict]:
        """解析 $ref（#/components/schemas/Xxx）"""
        if not ref.startswith("#/"):
            return None

        parts = ref[2:].split("/")
        current = self.swagger
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    # ============================================================
    # 辅助：路径匹配
    # ============================================================
    def _match_path_filter(self, path: str) -> bool:
        """检查路径是否匹配过滤器（支持 * 通配符）"""
        if not self.path_filter:
            return True

        pattern = re.escape(self.path_filter).replace(r"\*", ".*")
        return bool(re.match(pattern, path))

    # ============================================================
    # 辅助：根据路径推断业务字段
    # ============================================================
    def _enrich_body_by_path(self, path: str, body: Any) -> Any:
        """根据路径补充业务字段（如 /pay/* 加入 return_code）"""
        if not isinstance(body, dict):
            return body

        if "/pay/" in path:
            body.setdefault("return_code", "SUCCESS")
            body.setdefault("result_code", "SUCCESS")
        elif "/sms/" in path:
            body.setdefault("code", "OK")
            body.setdefault("message", "短信发送成功")
        elif "/logistics/" in path or "/srm/" in path:
            body.setdefault("code", 20000)
            body.setdefault("message", "操作成功")

        return body


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="从 Swagger 自动生成 Mock 规则文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 生成全部接口
    python scripts/generate_mock.py --swagger=api.json --output=payment_gateway.json

    # 只生成 payment tag
    python scripts/generate_mock.py --swagger=api.json --tag=payment --output=pay.json

    # 只生成匹配 /pay/* 的路径
    python scripts/generate_mock.py --swagger=api.json --path-filter="/pay/*" --output=pay.json
        """,
    )
    parser.add_argument("--swagger", required=True, help="Swagger/OpenAPI 文件路径")
    parser.add_argument("--output", required=True, help="输出 Mock 规则文件名（相对 data/mocks/）")
    parser.add_argument("--tag", help="只生成指定 tag 的接口")
    parser.add_argument("--path-filter", help="路径过滤器（支持 * 通配符）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        gen = MockGenerator(
            swagger_path=args.swagger,
            tag_filter=args.tag,
            path_filter=args.path_filter,
        )
        result = gen.generate(output_name=args.output.replace(".json", ""))

        # 输出路径
        output_path = PROJECT_ROOT / "data" / "mocks" / args.output
        if not output_path.suffix:
            output_path = output_path.with_suffix(".json")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ Mock 规则已生成: {output_path}")
        logger.info(f"   规则数量: {len(result['rules'])}")
        logger.info(f"   提示：请手动检查关键字段是否符合真实场景")

    except Exception as e:
        logger.error(f"❌ 生成失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()