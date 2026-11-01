# -*- coding: utf-8 -*-
"""
scripts/init_yaml.py
--------------------
YAML 测试用例骨架自动生成脚本

功能：
    1. 从 Swagger/OpenAPI 生成 YAML 骨架（主要功能）
    2. 按 tag 批量生成（如电商、物流分模块）
    3. 自动推断请求参数类型（必填/可选、类型）
    4. 自动补充通用断言（status_code / error_code）
    5. 保留 TODO 标记供人工补充
    6. 支持 --dry-run 预览

用法：
    # 从 Swagger 生成单模块
    python scripts/init_yaml.py --swagger=api.json --tag=ecommerce --output-dir=testcases/ecommerce

    # 从 Swagger 生成并指定文件名
    python scripts/init_yaml.py --swagger=api.json --path="/dar/user/login" --output=testcases/ecommerce/user_login.yaml

    # 批量生成（每个 tag 一个目录）
    python scripts/init_yaml.py --swagger=api.json --all-tags --output-dir=testcases
"""
import os
import re
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("init_yaml")

# ============================================================
# 字段名 → 智能默认值（覆盖常见业务字段）
# ============================================================
FIELD_VALUE_MAP = {
    # 用户相关
    r"user_?name|username": "{{env.accounts.admin.username}}",
    r"passwd|password|pwd": "{{env.accounts.admin.password}}",
    r"phone|mobile|tel": "{{faker.unique_phone}}",
    r"email|mail": "{{faker.email}}",
    r"name": "{{faker.name}}",
    r"role_?id": 123456789,
    r"dates|expire|deadline": "{{time.offset_days(365)}}",
    r"token": "{{token}}",

    # 订单相关
    r"goods_?id|product_?id": "18382788819",
    r"number|count|quantity|num": 1,
    r"order_?no|order_?number|out_trade_no": "{{order_no}}",
    r"user_?id": "{{user_id}}",
    r"price|amount|fee|total": "128",
    r"time_?stamp": "{{time.now_timestamp}}",
    r"remark|comment|desc": "自动化测试数据-{{faker.uuid}}",
    r"address|addr": "{{faker.address}}",
    r"consignee|receiver": "{{faker.name}}",

    # 物流相关
    r"carrier_?id": "{{carrier_id}}",
    r"order_?id": "{{order_no}}",
    r"schedule_?no": "{{schedule_no}}",
    r"item_?id|material_?id": "1676511586856882178",
    r"dispatch_?num": 50,
    r"vehicle_?no": "川A{{faker.regexify('[0-9]{5}')}}",
    r"driver_?name": "{{faker.name}}",
    r"driver_?phone": "{{faker.unique_phone}}",

    # 通用
    r"id$|_id$": 1,
    r"status|state": "1",
    r"page": 1,
    r"size|page_size|limit": 20,
    r"current": 1,
    r"action_?time|start_?time|end_?time": "{{time.now_str}}",
}

# 已知接口的默认断言
INTERFACE_ASSERTIONS = {
    "/dar/user/login": {
        "extract": {"token": "$.token"},
        "assertions": [
            {"eq": ["$.msg_code", 200]},
            {"contains": ["$.msg", "成功"]},
            {"not_null": ["$.token"]},
        ],
    },
    "/char/user/addUser": {
        "assertions": [
            {"eq": ["$.msg_code", 200]},
        ],
    },
    "/coupApply/cms/placeAnOrder": {
        "extract": {"order_no": "$.orderNumber", "user_id": "$.userId"},
        "assertions": [
            {"eq": ["$.error_code", "0000"]},
            {"regex": ["$.orderNumber", "^\\d{20,22}$"]},
        ],
    },
    "/api/order/customer/orderPlan/create": {
        "extract": {"order_no": "$.orderNo"},
        "assertions": [
            {"eq": ["$.code", 20000]},
            {"not_null": ["$.orderNo"]},
        ],
    },
}


# ============================================================
# YAML 生成器
# ============================================================
class YamlGenerator:
    """YAML 测试用例骨架生成器"""

    def __init__(self, swagger_path: str):
        self.swagger_path = Path(swagger_path)
        self.swagger = self._load_swagger()
        self._openapi_version = self.swagger.get("openapi") or self.swagger.get("swagger")

    def _load_swagger(self) -> dict:
        if not self.swagger_path.exists():
            raise FileNotFoundError(f"Swagger 文件不存在: {self.swagger_path}")
        with open(self.swagger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"加载 Swagger: {self.swagger_path.name} (版本 {data.get('openapi') or data.get('swagger')})")
        return data

    # ============================================================
    # 生成单接口 YAML
    # ============================================================
    def generate_single(
            self,
            path: str,
            method: str,
            tag: Optional[str] = None,
    ) -> Optional[str]:
        """生成单个接口的 YAML 骨架"""
        spec = self.swagger["paths"].get(path, {}).get(method.lower())
        if not spec:
            return None

        # 1. 基础信息
        interface_name = spec.get("summary") or spec.get("operationId") or f"{method.upper()} {path}"
        tags = spec.get("tags", [])
        if tag and tag not in tags:
            return None

        # 2. 请求体
        request_body = self._build_request_body(path, method, spec)

        # 3. 断言（智能补充）
        extract, validations = self._build_assertions(path, method, spec)

        # 4. 组装
        yaml_content = self._render_yaml(
            interface_name=interface_name,
            method=method,
            path=path,
            tags=tags,
            request_body=request_body,
            extract=extract,
            validations=validations,
        )
        return yaml_content

    # ============================================================
    # 生成按 tag 分组的所有 YAML
    # ============================================================
    def generate_by_tag(self, tag: str) -> Dict[str, str]:
        """按 tag 生成所有接口的 YAML（返回 {filename: content}）"""
        results = {}
        for path, methods in self.swagger.get("paths", {}).items():
            for method, spec in methods.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                if tag not in spec.get("tags", []):
                    continue
                content = self.generate_single(path, method, tag)
                if content:
                    filename = self._path_to_filename(path, method)
                    results[filename] = content
        return results

    # ============================================================
    # 构建请求体
    # ============================================================
    def _build_request_body(self, path: str, method: str, spec: dict) -> dict:
        """构建 request 节点"""
        body = {
            "method": method.upper(),
            "url": path,
        }
        headers = {}
        json_body = None
        form_data = None

        # 1. 从 parameters 提取 Query / Header / Path
        for param in spec.get("parameters", []):
            param_in = param.get("in")
            param_name = param.get("name")
            if not param_name:
                continue
            default_value = self._smart_value(param_name, param.get("schema") or param)

            if param_in == "header":
                headers[param_name] = str(default_value)
            elif param_in == "query":
                body.setdefault("params", {})[param_name] = default_value
            elif param_in == "path":
                # path 参数替换到 URL
                body["url"] = body["url"].replace(f"{{{param_name}}}", str(default_value))

        # 2. 从 requestBody（OpenAPI 3.0）提取 JSON 请求体
        request_body = spec.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            if "application/json" in content:
                schema = content["application/json"].get("schema", {})
                json_body = self._generate_from_schema(schema)
            elif "application/x-www-form-urlencoded" in content:
                schema = content["application/x-www-form-urlencoded"].get("schema", {})
                form_data = self._generate_from_schema(schema)

        # 3. 从 parameters（OpenAPI 2.0）提取 body 请求体
        for param in spec.get("parameters", []):
            if param.get("in") == "body":
                schema = param.get("schema", {})
                json_body = self._generate_from_schema(schema)
                break
            if param.get("in") == "formData":
                form_data = form_data or {}
                form_data[param["name"]] = self._smart_value(param["name"], param.get("schema") or param)

        # 4. 智能推断 Content-Type
        if json_body is not None:
            headers.setdefault("Content-Type", "application/json; charset=UTF-8")
            body["json"] = json_body
        elif form_data is not None:
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            body["data"] = form_data

        if headers:
            body["headers"] = headers

        return body

    # ============================================================
    # 从 Schema 生成示例数据
    # ============================================================
    def _generate_from_schema(self, schema: dict, depth: int = 0) -> Any:
        """递归生成符合 Schema 的示例数据"""
        if depth > 5 or not schema:
            return None

        if "$ref" in schema:
            schema = self._resolve_ref(schema["$ref"])
            if not schema:
                return None

        if "enum" in schema:
            return schema["enum"][0]

        if "example" in schema:
            return schema["example"]

        schema_type = schema.get("type", "object")

        if schema_type == "object":
            properties = schema.get("properties", {})
            result = {}
            for prop_name, prop_schema in properties.items():
                result[prop_name] = self._smart_value(prop_name, prop_schema)
            return result
        elif schema_type == "array":
            return [self._generate_from_schema(schema.get("items", {}), depth + 1)]
        else:
            return self._generate_primitive(schema)

    def _generate_primitive(self, schema: dict) -> Any:
        """生成基础类型"""
        if "example" in schema:
            return schema["example"]
        t = schema.get("type", "string")
        return {"string": "sample", "integer": 0, "number": 0.0, "boolean": True}.get(t, None)

    def _resolve_ref(self, ref: str) -> Optional[dict]:
        """解析 $ref"""
        if not ref.startswith("#/"):
            return None
        parts = ref[2:].split("/")
        cur = self.swagger
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur

    # ============================================================
    # 智能生成字段值
    # ============================================================
    def _smart_value(self, field_name: str, schema: dict = None) -> Any:
        """根据字段名和 Schema 生成智能默认值"""
        lower = field_name.lower()

        # 优先字段名匹配
        for pattern, value in FIELD_VALUE_MAP.items():
            if re.search(pattern, lower):
                return value

        # 其次 Schema 类型
        if schema:
            t = schema.get("type", "string")
            if "enum" in schema:
                return schema["enum"][0]
            if "example" in schema:
                return schema["example"]
            return {"string": "sample", "integer": 0, "number": 0.0, "boolean": True}.get(t, None)

        return "sample"

    # ============================================================
    # 智能断言
    # ============================================================
    def _build_assertions(self, path: str, method: str, spec: dict):
        """根据路径推断默认断言和提取规则"""
        extract = {}
        assertions = [{"eq": ["status_code", 200]}]

        # 已知接口断言
        for known_path, config in INTERFACE_ASSERTIONS.items():
            if path == known_path:
                extract.update(config.get("extract", {}))
                assertions.extend(config.get("assertions", []))
                return extract, assertions

        # 通用推断
        if "/logistics/" in path or "/srm/" in path:
            assertions.extend([
                {"eq": ["$.code", 20000]},
                {"contains": ["$.message", "成功"]},
            ])
        elif "/coupApply/" in path or "/char/" in path:
            assertions.extend([
                {"eq": ["$.error_code", "0000"]},
            ])
        elif "/dar/" in path:
            assertions.append({"eq": ["$.msg_code", 200]})

        # 智能提取 orderNo
        if any(kw in path for kw in ["placeAnOrder", "orderPlan", "create"]):
            if "/logistics/" in path:
                extract["order_no"] = "$.orderNo"
            else:
                extract["order_no"] = "$.orderNumber"

        return extract, assertions

    # ============================================================
    # 渲染 YAML 文本
    # ============================================================
    def _render_yaml(
            self,
            interface_name: str,
            method: str,
            path: str,
            tags: List[str],
            request_body: dict,
            extract: dict,
            validations: List[dict],
    ) -> str:
        """渲染 YAML 文本（带 TODO 注释）"""
        lines = [
            f"# ============================================================",
            f"# {interface_name}",
            f"# 自动生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# 接口路径: {method.upper()} {path}",
            f"# TODO: 请补充真实业务数据、断言规则、清理逻辑",
            f"# ============================================================",
            f"- config:",
            f"    name: \"{interface_name}\"",
            f"    priority: \"P2\"                 # TODO: P0 / P1 / P2 / P3",
            f"    tags: {json.dumps(tags or ['todo'], ensure_ascii=False)}",
            "",
        ]

        # 如果涉及认证，加 setup
        if "/char/" in path or "/coupApply/" in path:
            lines.extend([
                "- setup:",
                f"    - include: \"admin_login.yaml\"   # TODO: 如已通过 Context 注入 token 可删",
                "",
            ])

        # test 节点
        lines.append("- test:")
        lines.append(f"    name: \"{interface_name}\"")
        lines.append("    request:")
        lines.extend(self._render_request(request_body))

        # extract
        if extract:
            lines.append("    extract:")
            for k, v in extract.items():
                lines.append(f"      {k}: \"{v}\"")

        # validate
        lines.append("    validate:")
        for assert_rule in validations:
            for assert_type, args in assert_rule.items():
                lines.append(f"      - {assert_type}: {json.dumps(args, ensure_ascii=False)}")

        # teardown 占位（可选）
        lines.extend([
            "",
            "# - teardown:",
            "#     - test:",
            "#         name: \"清理数据\"",
            "#         request: { ... }",
            "#         ignore_errors: true",
        ])

        return "\n".join(lines) + "\n"

    def _render_request(self, body: dict, indent: int = 6) -> List[str]:
        """渲染 request 节点"""
        lines = []
        pad = " " * indent

        lines.append(f"{pad}method: {body['method']}")
        lines.append(f"{pad}url: \"{body['url']}\"")

        if "headers" in body:
            lines.append(f"{pad}headers:")
            for k, v in body["headers"].items():
                lines.append(f"{pad}  {k}: \"{v}\"")

        if "params" in body:
            lines.append(f"{pad}params:")
            for k, v in body["params"].items():
                lines.append(f"{pad}  {k}: {json.dumps(v, ensure_ascii=False)}")

        if "json" in body:
            lines.append(f"{pad}json:")
            lines.extend(self._render_dict(body["json"], indent + 2))

        if "data" in body:
            lines.append(f"{pad}data:")
            lines.extend(self._render_dict(body["data"], indent + 2))

        return lines

    def _render_dict(self, data: Any, indent: int) -> List[str]:
        """递归渲染 dict/list"""
        lines = []
        pad = " " * indent

        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    lines.append(f"{pad}{k}:")
                    lines.extend(self._render_dict(v, indent + 2))
                elif isinstance(v, list):
                    lines.append(f"{pad}{k}:")
                    lines.extend(self._render_dict(v, indent + 2))
                else:
                    lines.append(f"{pad}{k}: {self._render_value(v)}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(f"{pad}-")
                    lines.extend(self._render_dict(item, indent + 2))
                else:
                    lines.append(f"{pad}- {self._render_value(item)}")

        return lines

    @staticmethod
    def _render_value(v: Any) -> str:
        """渲染单个值"""
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            # 占位符不加引号
            if v.startswith("{{") and v.endswith("}}"):
                return f'"{v}"'
            return f'"{v}"'
        return json.dumps(v, ensure_ascii=False)

    # ============================================================
    # 文件名推断
    # ============================================================
    @staticmethod
    def _path_to_filename(path: str, method: str) -> str:
        """从路径和 method 推断文件名"""
        # 去掉首尾斜杠，替换 / 为 _
        name = path.strip("/").replace("/", "_").replace("-", "_")
        # 去掉 path 参数
        name = re.sub(r"\{[^}]+\}", "", name)
        # 加上 method
        name = f"{method.lower()}_{name}"
        # 去掉多余下划线
        name = re.sub(r"_+", "_", name).strip("_")
        return f"{name}.yaml"


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="API_TEST YAML 用例骨架生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 从 Swagger 生成电商模块
    python scripts/init_yaml.py --swagger=api.json --tag=ecommerce --output-dir=testcases/ecommerce

    # 生成单个接口
    python scripts/init_yaml.py --swagger=api.json --path=/dar/user/login --output=testcases/ecommerce/user_login.yaml

    # 按所有 tag 批量生成
    python scripts/init_yaml.py --swagger=api.json --all-tags --output-dir=testcases
        """,
    )
    parser.add_argument("--swagger", required=True, help="Swagger/OpenAPI 文件路径")
    parser.add_argument("--tag", help="只生成指定 tag 的接口")
    parser.add_argument("--all-tags", action="store_true", help="按所有 tag 分组生成")
    parser.add_argument("--path", help="指定单个接口路径")
    parser.add_argument("--method", default="post", help="配合 --path 指定方法（默认 post）")
    parser.add_argument("--output", help="单文件输出路径（配合 --path 使用）")
    parser.add_argument("--output-dir", help="输出目录（tag 模式使用）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的文件")
    parser.add_argument("--dry-run", action="store_true", help="只展示不写入")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        gen = YamlGenerator(swagger_path=args.swagger)

        # 模式 1：单接口
        if args.path:
            if not args.output:
                parser.error("--path 需要配合 --output 使用")
            content = gen.generate_single(args.path, args.method)
            if not content:
                logger.error(f"未找到接口: {args.path} [{args.method}]")
                sys.exit(1)

            output = PROJECT_ROOT / args.output
            if output.exists() and not args.overwrite:
                logger.error(f"文件已存在（如需覆盖请加 --overwrite）: {output}")
                sys.exit(1)

            if args.dry_run:
                print(content)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content, encoding="utf-8")
                logger.info(f"✅ 已生成: {output}")
            return

        # 模式 2：按 tag 生成
        if args.tag:
            if not args.output_dir:
                parser.error("--tag 需要配合 --output-dir 使用")
            results = gen.generate_by_tag(args.tag)
            if not results:
                logger.warning(f"未找到 tag={args.tag} 的接口")
                sys.exit(1)

            output_dir = PROJECT_ROOT / args.output_dir
            for filename, content in results.items():
                output_path = output_dir / filename
                if output_path.exists() and not args.overwrite:
                    logger.warning(f"跳过（已存在）: {output_path}")
                    continue
                if args.dry_run:
                    print(f"--- {filename} ---")
                    print(content)
                else:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(content, encoding="utf-8")
                    logger.info(f"✅ 已生成: {output_path}")
            return

        # 模式 3：全 tag 批量
        if args.all_tags:
            if not args.output_dir:
                parser.error("--all-tags 需要配合 --output-dir 使用")

            # 收集所有 tag
            all_tags = set()
            for methods in gen.swagger.get("paths", {}).values():
                for spec in methods.values():
                    if isinstance(spec, dict):
                        all_tags.update(spec.get("tags", []))

            for tag in sorted(all_tags):
                logger.info(f"生成 tag={tag} 的用例")
                results = gen.generate_by_tag(tag)
                tag_dir = PROJECT_ROOT / args.output_dir / tag
                for filename, content in results.items():
                    output_path = tag_dir / filename
                    if output_path.exists() and not args.overwrite:
                        continue
                    if args.dry_run:
                        print(f"--- {tag}/{filename} ---")
                    else:
                        tag_dir.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(content, encoding="utf-8")
                        logger.info(f"✅ 已生成: {output_path}")
            return

        parser.error("必须指定 --tag / --path / --all-tags 之一")

    except Exception as e:
        logger.error(f"❌ 生成失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()