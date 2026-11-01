# -*- coding: utf-8 -*-
"""
common/client/auth.py
---------------------
认证管理器（AuthManager）
注意：电商登录接口路径为 /dar/user/login（请留意拼写，不是 /da/）
"""
import os
import time
import threading
import logging
from typing import Optional, Dict, Any

import yaml
from jsonpath_ng import parse as jsonpath_parse

from common.exceptions import (
    AuthError,
    TokenExpiredError,
    CredentialMissingError,
)

logger = logging.getLogger(__name__)


class _TokenCache:
    """单个 Token 的缓存结构"""
    __slots__ = ("token", "expire_at", "raw")

    def __init__(self, token: str, expire_at: float, raw: Dict[str, Any]):
        self.token = token
        self.expire_at = expire_at
        self.raw = raw

    def is_valid(self, safety_window: int = 60) -> bool:
        return time.time() < (self.expire_at - safety_window)


class AuthManager:
    """
    认证管理器 - 负责获取、缓存、注入 Token / Cookie
    """
    _token_cache: Dict[str, _TokenCache] = {}
    _lock = threading.RLock()

    def __init__(self, api_client, env: str = "test", config_dir: str = "config/env"):
        self.client = api_client
        self.env = env
        self.config_dir = config_dir
        self._env_config = self._load_env_config()

    def _load_env_config(self) -> Dict[str, Any]:
        cfg_file = os.path.join(self.config_dir, f"{self.env}.yaml")
        if not os.path.exists(cfg_file):
            raise AuthError(f"环境配置文件不存在: {cfg_file}")
        with open(cfg_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _get_credentials(self, role: str) -> Dict[str, str]:
        """获取指定角色的账号密码"""
        accounts = self._env_config.get("accounts", {})
        role_cfg = accounts.get(role, {})
        if not role_cfg:
            raise CredentialMissingError(f"环境 {self.env} 未配置角色账号: {role}")

        env_prefix = f"API_TEST_{self.env.upper()}_{role.upper()}"
        username = os.getenv(f"{env_prefix}_USER") or role_cfg.get("username")
        password = os.getenv(f"{env_prefix}_PASSWORD") or role_cfg.get("password")

        if not username or not password:
            raise CredentialMissingError(f"角色 {role} 的用户名或密码缺失（env={self.env}）")
        return {"username": username, "password": password}

    # ============================================================
    # 电商项目：Token 认证
    # ============================================================
    def get_token(self, role: str = "admin", force_refresh: bool = False) -> str:
        """获取电商项目的 Token"""
        cache_key = f"{self.env}:{role}"

        if not force_refresh:
            with self._lock:
                cached = self._token_cache.get(cache_key)
                if cached and cached.is_valid():
                    logger.debug(f"[Auth] 命中 Token 缓存: {cache_key}")
                    return cached.token

        logger.info(f"[Auth] 登录获取 Token: env={self.env}, role={role}")
        token, expire_at, raw = self._login_ecommerce(role)

        with self._lock:
            self._token_cache[cache_key] = _TokenCache(token, expire_at, raw)

        return token

    def _login_ecommerce(self, role: str):
        """调用电商登录接口，返回 (token, expire_at, raw)"""
        creds = self._get_credentials(role)
        login_cfg = self._env_config.get("auth", {}).get("ecommerce_login", {})

        # 修正默认路径为 /dar/user/login
        login_url = login_cfg.get("url", "/dar/user/login")
        content_type = login_cfg.get(
            "content_type", "application/x-www-form-urlencoded;charset=UTF-8"
        )
        token_jsonpath = login_cfg.get("token_jsonpath", "$.token")
        expire_field = login_cfg.get("expire_seconds", 3600)

        payload = {
            "user_name": creds["username"],
            "passwd": creds["password"],
        }

        logger.debug(f"[Auth] 发送登录请求到 {login_url}，表单参数: {payload}")

        # 依据文档严格使用表单提交
        resp = self.client.post(
            login_url,
            data=payload,
            headers={"Content-Type": content_type},
        )

        if resp.status_code != 200:
            raise AuthError(
                f"登录接口 HTTP 状态异常: {resp.status_code}, body={resp.text[:300]}"
            )

        try:
            body = resp.json()
        except Exception:
            raise AuthError(f"登录接口返回非 JSON 格式: {resp.text[:300]}")

        # 依据文档：msg_code == 200 表示成功
        msg_code = body.get("msg_code")
        if msg_code not in (200, "200", None):
            raise AuthError(
                f"登录业务失败: msg_code={msg_code}, msg={body.get('msg')}"
            )

        # JSONPath 提取 token
        matches = jsonpath_parse(token_jsonpath).find(body)
        if not matches:
            raise AuthError(
                f"登录成功但未在响应中提取到 Token（{token_jsonpath}）: {body}"
            )

        token_value = str(matches[0].value)
        expire_at = time.time() + int(expire_field)

        logger.info(f"[Auth] 登录成功: role={role}, token={token_value[:12]}...")
        return token_value, expire_at, body

    def inject_token(self, role: str = "admin", header_name: str = "token") -> None:
        """将 Token 注入到 session 和 context"""
        token = self.get_token(role=role)
        self.client.session.headers.update({header_name: token})

        from common.context import context
        context.set("token", token)
        context.set(f"token_{role}", token)

    # ============================================================
    # 物流项目：Cookie 认证
    # ============================================================
    def login_logistics(self, role: str = "logistics", force_refresh: bool = False) -> Dict[str, str]:
        """物流项目登录，返回 Cookie 字典"""
        cache_key = f"{self.env}:{role}:cookie"

        if not force_refresh:
            with self._lock:
                cached = self._token_cache.get(cache_key)
                if cached and cached.is_valid():
                    return cached.raw.get("cookies", {})

        creds = self._get_credentials(role)
        login_cfg = self._env_config.get("auth", {}).get("logistics_login", {})

        login_url = login_cfg.get("url", "/api/user/login")
        cookie_jsonpath = login_cfg.get("cookie_jsonpath")
        expire_field = login_cfg.get("expire_seconds", 1800)

        payload = {
            "username": creds["username"],
            "password": creds["password"],
        }

        resp = self.client.post(
            login_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise AuthError(f"物流登录失败: HTTP {resp.status_code}, {resp.text[:300]}")

        body = resp.json()
        if body.get("code") != 20000:
            raise AuthError(f"物流登录失败: code={body.get('code')}, message={body.get('message')}")

        cookies = dict(resp.cookies) or dict(self.client.session.cookies)

        if not cookies and cookie_jsonpath:
            matches = jsonpath_parse(cookie_jsonpath).find(body)
            if matches:
                cookies = {"JSESSIONID": str(matches[0].value)}

        if not cookies:
            raise AuthError("物流登录成功，但未获取到任何 Cookie")

        expire_at = time.time() + int(expire_field)
        with self._lock:
            self._token_cache[cache_key] = _TokenCache(
                token="cookie-session",
                expire_at=expire_at,
                raw={"cookies": cookies, "body": body},
            )

        from common.context import context
        context.set("cookies", cookies)
        context.set(f"cookies_{role}", cookies)

        logger.info(f"[Auth] 物流登录成功: role={role}, cookies={list(cookies.keys())}")
        return cookies

    def inject_cookies(self, role: str = "logistics") -> None:
        """将 Cookie 注入到 session"""
        cookies = self.login_logistics(role=role)
        for k, v in cookies.items():
            self.client.session.cookies.set(k, v)

        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        self.client.session.headers.update({"Cookie": cookie_str})

    # ============================================================
    # 统一入口
    # ============================================================
    def inject_headers(self, role: str = "admin", project: str = "ecommerce") -> None:
        """统一注入鉴权信息"""
        if project == "ecommerce":
            self.inject_token(role=role)
        elif project == "logistics":
            self.inject_cookies(role=role)
        else:
            raise ValueError(f"未知项目类型: {project}")

    def clear_cache(self, role: Optional[str] = None) -> None:
        """清理 Token 缓存"""
        with self._lock:
            if role is None:
                self._token_cache.clear()
                logger.info("[Auth] 已清空全部 Token 缓存")
            else:
                keys_to_remove = [k for k in self._token_cache if role in k]
                for k in keys_to_remove:
                    del self._token_cache[k]
                logger.info(f"[Auth] 已清空角色 {role} 的 Token 缓存")