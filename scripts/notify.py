# -*- coding: utf-8 -*-
"""
scripts/notify.py
-----------------
测试结果通知发送脚本

功能：
    1. 读取质量报告（quality-report.json）
    2. 通过邮件、钉钉、企业微信发送结果
    3. 根据状态区分通知级别（成功 / 失败 / 不稳定 / 中断）
    4. 支持失败用例 Top 5、报告链接、构建信息
    5. 支持 dry-run 预览

用法：
    # 发送到所有通道（默认配置）
    python scripts/notify.py --status=SUCCESS --env=test

    # 只发邮件
    python scripts/notify.py --status=FAILURE --channels=email

    # 指定报告和质量报告文件
    python scripts/notify.py --status=FAILURE \
        --quality-report=reports/artifacts/quality-report.json \
        --report-url=http://jenkins/job/api-test/42/allure
"""
import os
import sys
import json
import time
import smtplib
import argparse
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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
logger = logging.getLogger("notify")

# ============================================================
# 状态颜色映射
# ============================================================
STATUS_COLOR = {
    "SUCCESS": "#52c41a",
    "UNSTABLE": "#faad14",
    "FAILURE": "#f5222d",
    "ABORTED": "#8c8c8c",
}

STATUS_ICON = {
    "SUCCESS": "✅",
    "UNSTABLE": "⚠️",
    "FAILURE": "❌",
    "ABORTED": "⏹️",
}


# ============================================================
# 消息内容构建
# ============================================================
class MessageBuilder:
    """消息内容构建器（邮件/钉钉共用的数据结构）"""

    def __init__(
            self,
            status: str,
            env: str,
            quality_report: Optional[dict] = None,
            metadata: Optional[dict] = None,
    ):
        self.status = status.upper()
        self.env = env
        self.quality_report = quality_report or {}
        self.metadata = metadata or {}

    def build_html(self) -> str:
        """构建 HTML 消息（用于邮件）"""
        icon = STATUS_ICON.get(self.status, "❓")
        color = STATUS_COLOR.get(self.status, "#333")

        summary = self.quality_report.get("summary", {})
        failed_cases = self.quality_report.get("failed_cases", [])

        # 失败用例 Top 5
        failed_html = ""
        if failed_cases:
            failed_html = "<h3>失败用例（Top 5）</h3><ul>"
            for case in failed_cases[:5]:
                failed_html += (
                    f"<li><b>[{case.get('priority', '?')}] {case.get('name', '?')}</b>: "
                    f"{case.get('error', '未知错误')}</li>"
                )
            failed_html += "</ul>"

        # 构建信息
        meta_html = ""
        if self.metadata:
            meta_html = "<table border='1' cellspacing='0' cellpadding='8' style='border-collapse:collapse;width:100%;'>"
            for k, v in self.metadata.items():
                meta_html += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
            meta_html += "</table>"

        # 质量摘要
        summary_html = ""
        if summary:
            summary_html = f"""
            <h3>质量摘要</h3>
            <table border='1' cellspacing='0' cellpadding='8' style='border-collapse:collapse;width:100%;'>
              <tr><td><b>总数</b></td><td>{summary.get('total', 0)}</td></tr>
              <tr><td><b>通过</b></td><td style='color:#52c41a'>{summary.get('passed', 0)}</td></tr>
              <tr><td><b>失败</b></td><td style='color:#f5222d'>{summary.get('failed', 0)}</td></tr>
              <tr><td><b>跳过</b></td><td>{summary.get('skipped', 0)}</td></tr>
              <tr><td><b>通过率</b></td><td>{summary.get('pass_rate', '-')}</td></tr>
              <tr><td><b>P0 通过率</b></td><td>{summary.get('p0_pass_rate', '-')}</td></tr>
              <tr><td><b>耗时</b></td><td>{summary.get('duration', 0)}s</td></tr>
            </table>
            """

        # 报告链接
        report_url = self.metadata.get("报告地址", "")
        report_button = ""
        if report_url:
            report_button = f"""
            <p style="margin-top:20px;">
              <a href="{report_url}" style="padding:10px 20px;background:#1890ff;
                 color:#fff;text-decoration:none;border-radius:4px;">
                 📊 查看 Allure 报告
              </a>
            </p>
            """

        html = f"""
        <div style="font-family:'Microsoft YaHei',Arial,sans-serif;max-width:720px;">
          <h2 style="color:{color};">{icon} API_TEST 自动化测试结果 —— {self.status}</h2>
          <p>环境: <b>{self.env}</b> | 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
          {meta_html}
          {summary_html}
          {failed_html}
          {report_button}
          <p style="color:#999;font-size:12px;margin-top:30px;">
            此邮件由 API_TEST 框架自动发送，请勿直接回复。
          </p>
        </div>
        """
        return html

    def build_markdown(self) -> str:
        """构建 Markdown 消息（用于钉钉/企业微信）"""
        icon = STATUS_ICON.get(self.status, "❓")
        summary = self.quality_report.get("summary", {})

        lines = [
            f"### {icon} API_TEST 测试结果 —— {self.status}",
            f"**环境**: {self.env}",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        if summary:
            lines.extend([
                "**质量摘要**:",
                f"- 总数: {summary.get('total', 0)}",
                f"- 通过: {summary.get('passed', 0)}",
                f"- 失败: {summary.get('failed', 0)}",
                f"- 通过率: {summary.get('pass_rate', '-')}",
                f"- P0 通过率: {summary.get('p0_pass_rate', '-')}",
                f"- 耗时: {summary.get('duration', 0)}s",
                "",
            ])

        failed_cases = self.quality_report.get("failed_cases", [])
        if failed_cases:
            lines.append(f"**失败用例 Top {min(5, len(failed_cases))}**:")
            for case in failed_cases[:5]:
                lines.append(
                    f"- [{case.get('priority', '?')}] {case.get('name', '?')}: "
                    f"{case.get('error', '未知')}"
                )
            lines.append("")

        report_url = self.metadata.get("报告地址", "")
        if report_url:
            lines.append(f"[📊 查看 Allure 报告]({report_url})")

        return "\n".join(lines)


# ============================================================
# 通知通道
# ============================================================
class BaseNotifier:
    """通知通道基类"""

    def send(self, message: MessageBuilder, to: str = "") -> bool:
        raise NotImplementedError


class EmailNotifier(BaseNotifier):
    """邮件通知"""

    def __init__(self, smtp_host, smtp_port, username, password, use_ssl=True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl

    def send(self, message: MessageBuilder, to: str = "") -> bool:
        if not to:
            logger.error("[Email] 收件人未配置")
            return False

        if not self.smtp_host:
            logger.error("[Email] SMTP 未配置")
            return False

        try:
            # 构建邮件
            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(
                f"[{message.status}] API_TEST 测试结果 - {message.env} - "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                "utf-8",
            )
            msg["From"] = self.username
            msg["To"] = to

            html_body = MIMEText(message.build_html(), "html", "utf-8")
            msg.attach(html_body)

            # 发送
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
                server.starttls()

            server.login(self.username, self.password)
            server.sendmail(self.username, [to], msg.as_string())
            server.quit()

            logger.info(f"✅ [Email] 邮件已发送到: {to}")
            return True

        except Exception as e:
            logger.error(f"❌ [Email] 发送失败: {e}")
            return False


class DingTalkNotifier(BaseNotifier):
    """钉钉机器人通知"""

    def __init__(self, webhook: str, secret: Optional[str] = None):
        self.webhook = webhook
        self.secret = secret

    def send(self, message: MessageBuilder, to: str = "") -> bool:
        if not self.webhook:
            logger.error("[DingTalk] Webhook 未配置")
            return False

        try:
            # 如果配置了加签，需要生成签名
            webhook = self.webhook
            if self.secret:
                webhook = self._sign_webhook(webhook, self.secret)

            # 构建请求
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"API_TEST - {message.status}",
                    "text": message.build_markdown(),
                },
                "at": {
                    "isAtAll": message.status in ("FAILURE", "UNSTABLE"),
                },
            }

            # 如果 @ 特定人员
            if to:
                payload["at"]["atMobiles"] = [m.strip() for m in to.split(",") if m.strip()]

            resp = requests.post(webhook, json=payload, timeout=10)
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info(f"✅ [DingTalk] 钉钉消息已发送")
                return True
            else:
                logger.error(f"❌ [DingTalk] 发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"❌ [DingTalk] 异常: {e}")
            return False

    @staticmethod
    def _sign_webhook(webhook: str, secret: str) -> str:
        """钉钉加签"""
        import hmac
        import hashlib
        import base64
        from urllib.parse import quote_plus

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = quote_plus(base64.b64encode(hmac_code))
        return f"{webhook}&timestamp={timestamp}&sign={sign}"


class WeComNotifier(BaseNotifier):
    """企业微信机器人通知"""

    def __init__(self, webhook: str):
        self.webhook = webhook

    def send(self, message: MessageBuilder, to: str = "") -> bool:
        if not self.webhook:
            logger.error("[WeCom] Webhook 未配置")
            return False

        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": message.build_markdown(),
                },
            }

            resp = requests.post(self.webhook, json=payload, timeout=10)
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info(f"✅ [WeCom] 企业微信消息已发送")
                return True
            else:
                logger.error(f"❌ [WeCom] 发送失败: {result}")
                return False

        except Exception as e:
            logger.error(f"❌ [WeCom] 异常: {e}")
            return False


# ============================================================
# 配置读取
# ============================================================
def get_email_config() -> dict:
    """获取邮件配置"""
    return {
        "smtp_host": os.getenv("SMTP_HOST", settings.get("alert.channels.email.smtp_host", "")),
        "smtp_port": int(os.getenv("SMTP_PORT", settings.get("alert.channels.email.smtp_port", 465))),
        "username": os.getenv("SMTP_USER", settings.get("alert.channels.email.smtp_user", "")),
        "password": os.getenv("SMTP_PASSWORD", settings.get("alert.channels.email.smtp_password", "")),
        "to": os.getenv("NOTIFY_EMAIL_TO",
                        settings.get("alert.channels.email.to", "18507113868@163.com")
                        if isinstance(settings.get("alert.channels.email.to"), str)
                        else ",".join(settings.get("alert.channels.email.to", ["18507113868@163.com"]))
                        ),
        "enabled": settings.get("alert.channels.email.enabled", True),
    }


def get_dingtalk_config() -> dict:
    """获取钉钉配置"""
    return {
        "webhook": os.getenv("DINGTALK_WEBHOOK",
                             settings.get("alert.channels.dingtalk.webhook", "")),
        "secret": os.getenv("DINGTALK_SECRET",
                            settings.get("alert.channels.dingtalk.secret", "")),
        "enabled": settings.get("alert.channels.dingtalk.enabled", False),
    }


def get_wecom_config() -> dict:
    """获取企业微信配置"""
    return {
        "webhook": os.getenv("WECOM_WEBHOOK",
                             settings.get("alert.channels.wecom.webhook", "")),
        "enabled": settings.get("alert.channels.wecom.enabled", False),
    }


# ============================================================
# 主流程
# ============================================================
def send_notifications(
        status: str,
        env: str,
        quality_report_path: Optional[str],
        report_url: str = "",
        build_url: str = "",
        build_number: str = "",
        channels: List[str] = None,
        dry_run: bool = False,
) -> bool:
    """发送通知"""

    # 1. 加载质量报告
    quality_report = {}
    if quality_report_path and Path(quality_report_path).exists():
        try:
            with open(quality_report_path, "r", encoding="utf-8") as f:
                quality_report = json.load(f)
            logger.info(f"加载质量报告: {quality_report_path}")
        except Exception as e:
            logger.warning(f"加载质量报告失败: {e}")

    # 2. 构建元数据
    metadata = {
        "构建号": build_number or os.getenv("BUILD_NUMBER", "N/A"),
        "环境": env,
        "触发者": os.getenv("BUILD_USER", "未知"),
        "Commit": os.getenv("GIT_COMMIT", "")[:8] or "N/A",
        "报告地址": report_url or os.getenv("BUILD_URL", "") + "allure",
        "构建地址": build_url or os.getenv("BUILD_URL", ""),
    }

    # 3. 构建消息
    message = MessageBuilder(
        status=status,
        env=env,
        quality_report=quality_report,
        metadata=metadata,
    )

    # 4. dry-run
    if dry_run:
        print("=" * 60)
        print("📧 邮件 HTML 预览")
        print("=" * 60)
        print(message.build_html())
        print("\n" + "=" * 60)
        print("📱 钉钉/企微 Markdown 预览")
        print("=" * 60)
        print(message.build_markdown())
        return True

    # 5. 发送
    channels = channels or ["email", "dingtalk", "wecom"]
    success = True

    # 邮件
    if "email" in channels:
        email_cfg = get_email_config()
        if email_cfg["enabled"] and email_cfg["smtp_host"]:
            notifier = EmailNotifier(
                smtp_host=email_cfg["smtp_host"],
                smtp_port=email_cfg["smtp_port"],
                username=email_cfg["username"],
                password=email_cfg["password"],
            )
            if not notifier.send(message, to=email_cfg["to"]):
                success = False
        else:
            logger.warning("[Email] 未配置或未启用，跳过")

    # 钉钉
    if "dingtalk" in channels:
        ding_cfg = get_dingtalk_config()
        if ding_cfg["enabled"] and ding_cfg["webhook"]:
            notifier = DingTalkNotifier(ding_cfg["webhook"], ding_cfg["secret"])
            if not notifier.send(message):
                success = False
        else:
            logger.warning("[DingTalk] 未配置或未启用，跳过")

    # 企业微信
    if "wecom" in channels:
        wecom_cfg = get_wecom_config()
        if wecom_cfg["enabled"] and wecom_cfg["webhook"]:
            notifier = WeComNotifier(wecom_cfg["webhook"])
            if not notifier.send(message):
                success = False
        else:
            logger.warning("[WeCom] 未配置或未启用，跳过")

    return success


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="API_TEST 测试结果通知脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 成功通知（只发邮件）
    python scripts/notify.py --status=SUCCESS --channels=email

    # 失败通知（全通道 + 钉钉 @所有人）
    python scripts/notify.py --status=FAILURE \
        --quality-report=reports/artifacts/quality-report.json

    # dry-run 预览消息
    python scripts/notify.py --status=FAILURE --dry-run
        """,
    )
    parser.add_argument("--status", required=True,
                        choices=["SUCCESS", "UNSTABLE", "FAILURE", "ABORTED"],
                        help="测试状态")
    parser.add_argument("--env", default=os.getenv("API_TEST_ENV", "test"),
                        help="环境名")
    parser.add_argument("--quality-report",
                        help="质量报告 JSON 路径（默认自动查找）")
    parser.add_argument("--report-url",
                        default=os.getenv("REPORT_URL", ""),
                        help="Allure 报告 URL")
    parser.add_argument("--build-url",
                        default=os.getenv("BUILD_URL", ""),
                        help="Jenkins 构建 URL")
    parser.add_argument("--build-number",
                        default=os.getenv("BUILD_NUMBER", ""),
                        help="Jenkins 构建号")
    parser.add_argument("--channels", default="email",
                        help="通道（逗号分隔）：email,dingtalk,wecom")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览，不实际发送")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 自动查找质量报告
    quality_report = args.quality_report
    if not quality_report:
        default_path = PROJECT_ROOT / "reports" / "artifacts" / "quality-report.json"
        if default_path.exists():
            quality_report = str(default_path)

    # 解析通道
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]

    # 发送
    success = send_notifications(
        status=args.status,
        env=args.env,
        quality_report_path=quality_report,
        report_url=args.report_url,
        build_url=args.build_url,
        build_number=args.build_number,
        channels=channels,
        dry_run=args.dry_run,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()