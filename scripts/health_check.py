# -*- coding: utf-8 -*-
"""
scripts/health_check.py
-----------------------
环境健康检查脚本

功能：
    1. 检查被测服务 API 是否可达（HTTP 200）
    2. 检查数据库连通性与表结构（可选）
    3. 检查 Redis 连通性（可选）
    4. 检查 Mock Server 端口可达
    5. 输出 JSON 健康报告（供 Jenkins 门禁消费）
    6. 支持 --skip-db / --skip-redis / --skip-mock 单独跳过

用法：
    # 检查全部
    python scripts/health_check.py --env=test

    # 只检查被测服务
    python scripts/health_check.py --env=test --skip-db --skip-redis --skip-mock

    # 输出 JSON 报告
    python scripts/health_check.py --env=test --output=reports/artifacts/health-check.json

    # 严格模式（任何 WARNING 也视为失败）
    python scripts/health_check.py --env=test --strict
"""
import os
import sys
import json
import socket
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings, get_current_env

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("health_check")


# ============================================================
# 单项检查结果
# ============================================================
class CheckItem:
    """单项检查结果"""

    def __init__(self, name: str):
        self.name = name
        self.status = "UNKNOWN"  # PASSED / WARNING / FAILED
        self.message = ""
        self.detail: Dict = {}
        self.duration_ms = 0

    def passed(self, message: str = "", **detail):
        self.status = "PASSED"
        self.message = message or "OK"
        self.detail.update(detail)
        return self

    def warning(self, message: str, **detail):
        self.status = "WARNING"
        self.message = message
        self.detail.update(detail)
        return self

    def failed(self, message: str, **detail):
        self.status = "FAILED"
        self.message = message
        self.detail.update(detail)
        return self

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


# ============================================================
# 健康检查器
# ============================================================
class HealthChecker:
    """环境健康检查器"""

    def __init__(self, strict: bool = False):
        self.strict = strict
        self.items: List[CheckItem] = []
        self._env = get_current_env()

    def add(self, item: CheckItem):
        self.items.append(item)
        icon = {"PASSED": "✅", "WARNING": "⚠️ ", "FAILED": "❌"}.get(item.status, "?")
        logger.info(f"{icon} [{item.name}] {item.status}: {item.message}")
        return item

    # ============================================================
    # 1. 被测服务 API 检查
    # ============================================================
    def check_api(self) -> CheckItem:
        """检查被测服务可达性"""
        item = CheckItem("API Service")
        base_url = settings.get("api.base_url", "")

        if not base_url:
            return self.add(item.failed("未配置 api.base_url"))

        item.detail["url"] = base_url
        item.detail["env"] = self._env

        try:
            import time
            start = time.time()
            resp = requests.get(
                base_url,
                timeout=10,
                verify=settings.get("api.verify_ssl", False),
            )
            item.duration_ms = round((time.time() - start) * 1000, 2)
            item.detail["status_code"] = resp.status_code
            item.detail["response_time_ms"] = item.duration_ms

            if resp.status_code < 400:
                return self.add(item.passed(
                    f"{base_url} 可达（HTTP {resp.status_code}）"
                ))
            elif resp.status_code < 500:
                return self.add(item.warning(
                    f"{base_url} 返回 HTTP {resp.status_code}（4xx 可能正常）",
                    status_code=resp.status_code,
                ))
            else:
                return self.add(item.failed(
                    f"{base_url} 返回 HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=resp.text[:200],
                ))

        except requests.exceptions.Timeout:
            return self.add(item.failed(
                f"{base_url} 请求超时（>10s）",
                error="timeout",
            ))
        except requests.exceptions.ConnectionError as e:
            return self.add(item.failed(
                f"{base_url} 连接失败",
                error=str(e)[:200],
            ))
        except Exception as e:
            return self.add(item.failed(
                f"{base_url} 未知错误",
                error=str(e)[:200],
            ))

    # ============================================================
    # 2. 数据库检查
    # ============================================================
    def check_database(self) -> CheckItem:
        """检查数据库连通性"""
        item = CheckItem("Database")

        if not settings.get("database.enabled", False):
            return self.add(item.warning("数据库未启用（database.enabled=false）"))

        host = settings.get("database.host")
        port = settings.get("database.port", 3306)
        db_name = settings.get("database.db_name")

        if not host:
            return self.add(item.failed("未配置 database.host"))

        item.detail.update({
            "host": host,
            "port": port,
            "database": db_name,
        })

        try:
            import time
            start = time.time()

            # 1. TCP 连通性
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()

            if result != 0:
                return self.add(item.failed(
                    f"{host}:{port} TCP 不可达",
                    tcp_result=result,
                ))

            # 2. 数据库连接 + 版本查询
            try:
                from common.db import DBClient
                client = DBClient(
                    host=host,
                    port=port,
                    user=settings.get("database.user"),
                    password=settings.get("database.password"),
                    database=db_name,
                    charset=settings.get("database.charset", "utf8mb4"),
                )

                # 查询版本
                result = client.query("SELECT VERSION()")
                version = result[0][0] if result else "unknown"

                # 查询表数量
                tables = client.query(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = %s",
                    params=(db_name,),
                )
                table_count = tables[0][0] if tables else 0

                client.close()
                item.duration_ms = round((time.time() - start) * 1000, 2)
                item.detail["version"] = version
                item.detail["table_count"] = table_count

                if table_count == 0:
                    return self.add(item.warning(
                        f"{host}:{port}/{db_name} 连接成功但无表",
                        version=version,
                        table_count=0,
                    ))

                return self.add(item.passed(
                    f"{host}:{port}/{db_name} 连接成功（{table_count} 张表）"
                ))

            except Exception as e:
                return self.add(item.failed(
                    f"{host}:{port} 数据库连接失败",
                    error=str(e)[:200],
                ))

        except Exception as e:
            return self.add(item.failed(
                f"数据库检查异常",
                error=str(e)[:200],
            ))

    # ============================================================
    # 3. Redis 检查
    # ============================================================
    def check_redis(self) -> CheckItem:
        """检查 Redis 连通性"""
        item = CheckItem("Redis")

        if not settings.get("redis.enabled", False):
            return self.add(item.warning("Redis 未启用（redis.enabled=false）"))

        host = settings.get("redis.host")
        port = settings.get("redis.port", 6379)

        if not host:
            return self.add(item.failed("未配置 redis.host"))

        item.detail.update({"host": host, "port": port})

        try:
            import time
            start = time.time()

            import redis
            client = redis.Redis(
                host=host,
                port=port,
                db=settings.get("redis.db", 0),
                socket_timeout=5,
            )

            pong = client.ping()
            info = client.info("server")

            item.duration_ms = round((time.time() - start) * 1000, 2)
            item.detail["version"] = info.get("redis_version", "unknown")
            item.detail["pong"] = pong

            if pong:
                return self.add(item.passed(
                    f"{host}:{port} PONG（版本 {item.detail['version']}）"
                ))
            else:
                return self.add(item.failed(
                    f"{host}:{port} PING 无响应"
                ))

        except Exception as e:
            return self.add(item.failed(
                f"{host}:{port} Redis 连接失败",
                error=str(e)[:200],
            ))

    # ============================================================
    # 4. Mock 服务检查
    # ============================================================
    def check_mock(self) -> CheckItem:
        """检查 Mock 服务端口可达"""
        item = CheckItem("Mock Server")

        if not settings.get("mock.enabled", False):
            return self.add(item.warning("Mock 未启用（mock.enabled=false）"))

        # 检查主端口
        port = settings.get("mock.port", 8788)
        host = "127.0.0.1"

        item.detail.update({"host": host, "port": port})

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                return self.add(item.passed(
                    f"{host}:{port} 可达"
                ))
            else:
                return self.add(item.warning(
                    f"{host}:{port} 端口不可达（Mock 未启动）",
                    tcp_result=result,
                ))

        except Exception as e:
            return self.add(item.failed(
                f"{host}:{port} 检查异常",
                error=str(e)[:200],
            ))

    # ============================================================
    # 5. 环境配置检查
    # ============================================================
    def check_config(self) -> CheckItem:
        """检查环境配置完整性"""
        item = CheckItem("Configuration")

        checks = {
            "env": self._env,
            "api.base_url": settings.get("api.base_url", ""),
            "database.enabled": settings.get("database.enabled", False),
            "snapshot.enabled": settings.get("snapshot.enabled", False),
        }
        item.detail.update(checks)

        missing = []
        if not self._env:
            missing.append("env")
        if not settings.get("api.base_url"):
            missing.append("api.base_url")

        if missing:
            return self.add(item.failed(
                f"缺少必需配置: {missing}",
                missing=missing,
            ))

        return self.add(item.passed(
            f"环境: {self._env}, base_url 已配置"
        ))

    # ============================================================
    # 主执行
    # ============================================================
    def run(
            self,
            skip_db: bool = False,
            skip_redis: bool = False,
            skip_mock: bool = False,
    ) -> dict:
        """执行所有检查"""
        logger.info("=" * 60)
        logger.info(f"🏥 环境健康检查开始 | 环境: {self._env}")
        logger.info("=" * 60)

        # 1. 配置检查
        self.check_config()

        # 2. 被测服务
        self.check_api()

        # 3. 数据库
        if not skip_db:
            self.check_database()

        # 4. Redis
        if not skip_redis:
            self.check_redis()

        # 5. Mock
        if not skip_mock:
            self.check_mock()

        # 汇总
        return self._build_report()

    def _build_report(self) -> dict:
        """生成健康检查报告"""
        summary = {
            "total": len(self.items),
            "passed": sum(1 for i in self.items if i.status == "PASSED"),
            "warnings": sum(1 for i in self.items if i.status == "WARNING"),
            "failed": sum(1 for i in self.items if i.status == "FAILED"),
        }
        summary["healthy"] = summary["failed"] == 0

        if self.strict and summary["warnings"] > 0:
            summary["healthy"] = False

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "env": self._env,
            "strict_mode": self.strict,
            "summary": summary,
            "checks": [item.to_dict() for item in self.items],
        }


# ============================================================
# 输出
# ============================================================
def print_report(report: dict):
    """格式化输出健康检查报告"""
    summary = report["summary"]

    print()
    print("=" * 60)
    print("  环境健康检查结果")
    print("=" * 60)
    print(f"  环境:     {report['env']}")
    print(f"  总检查数: {summary['total']}")
    print(f"  通过:     {summary['passed']} ✅")
    print(f"  警告:     {summary['warnings']} ⚠️")
    print(f"  失败:     {summary['failed']} ❌")
    print(f"  健康状态: {'✅ 健康' if summary['healthy'] else '❌ 不健康'}")
    print("=" * 60)

    if summary["failed"] > 0:
        print("\n❌ 失败项:")
        for item in report["checks"]:
            if item["status"] == "FAILED":
                print(f"  - {item['name']}: {item['message']}")
                if item.get("detail", {}).get("error"):
                    print(f"    错误: {item['detail']['error']}")

    if summary["warnings"] > 0:
        print("\n⚠️  警告项:")
        for item in report["checks"]:
            if item["status"] == "WARNING":
                print(f"  - {item['name']}: {item['message']}")

    print()


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="API_TEST 环境健康检查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 检查全部（按当前环境）
    python scripts/health_check.py --env=test

    # 只检查 API（跳过 DB/Redis/Mock）
    python scripts/health_check.py --env=test --skip-db --skip-redis --skip-mock

    # 严格模式（警告也失败）
    python scripts/health_check.py --env=test --strict

    # 输出 JSON 报告
    python scripts/health_check.py --env=test --output=reports/artifacts/health-check.json
        """,
    )
    parser.add_argument("--env", help="环境名（local/test/uat/prod）")
    parser.add_argument("--strict", action="store_true", help="严格模式")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库检查")
    parser.add_argument("--skip-redis", action="store_true", help="跳过 Redis 检查")
    parser.add_argument("--skip-mock", action="store_true", help="跳过 Mock 检查")
    parser.add_argument("--output", help="输出 JSON 报告路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    # 环境切换（通过环境变量）
    if args.env:
        os.environ["API_TEST_ENV"] = args.env
        # 重新加载配置（因为 settings 是单例）
        import importlib
        import config
        importlib.reload(config)
        # 强制重新读取 settings
        from config.settings import Settings
        new_settings = Settings()
        # 将新配置合并到全局
        for k, v in new_settings.__dict__.items():
            if not k.startswith("_"):
                setattr(settings, k, v)

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 执行
    try:
        checker = HealthChecker(strict=args.strict)
        report = checker.run(
            skip_db=args.skip_db,
            skip_redis=args.skip_redis,
            skip_mock=args.skip_mock,
        )

        # 输出
        print_report(report)

        # JSON 输出
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"📋 报告已写入: {output_path}")

        # 退出码
        sys.exit(0 if report["summary"]["healthy"] else 1)

    except KeyboardInterrupt:
        logger.warning("检查被中断")
        sys.exit(130)
    except Exception as e:
        logger.error(f"❌ 检查异常: {e}", exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()