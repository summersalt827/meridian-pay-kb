#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
身份认证 (账号口令校验 + HMAC 签名会话令牌)
============================================
修复原 web_ui.py 的越权漏洞: 角色此前由 POST 请求体声明, 任何人发一条
{"role":"admin"} 即可读取全部 internal 文档。本模块把「身份」改成服务端签发:

  - 口令: PBKDF2-HMAC-SHA256 + 每账号随机 salt 存储, 永不明文落盘
  - 令牌: v1.<base64url(payload)>.<base64url(HMAC-SHA256)>, payload 含
          用户名/角色/签发时间/过期时间/随机 jti, 服务端密钥签名
  - 校验: 签名比较用 hmac.compare_digest (恒定时间, 防时序侧信道)
  - 吊销: 内存级 jti 黑名单, /api/logout 后立即失效

角色与目录权限仍由 web_ui.ROLE_ALLOWED_DIRS 在服务端硬编码, 令牌只携带角色名,
不携带目录——目录映射永远不可由客户端影响。

生产接入: 把 load_directory_from_env() 换成企业 IdP (OIDC/SAML) 或内部用户服务,
只要最终能产出本模块的 Account 即可, 其余调用方无需改动。

依赖: 仅标准库。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

# ---- 常量 ----

TOKEN_VERSION = "v1"
DEFAULT_TTL_SECONDS = 8 * 3600                 # 会话默认 8 小时
PBKDF2_ITERATIONS = 200_000                    # OWASP 对 SHA256 的建议量级

# 内置演示账号 (仅用于本地验证; 生产必须换成真实用户源)
# 用户名沿用仓库既有测试里的 alice/sam/bob, 便于回归对照
DEMO_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("alice", "admin", "Alice（管理员）"),
    ("sam", "sales", "Sam（销售）"),
    ("bob", "viewer", "Bob（只读访客）"),
    ("cara", "compliance_officer", "Cara（合规官）"),
    ("ray", "risk_analyst", "Ray（风控分析师）"),
    ("erin", "card_ops", "Erin（卡运营）"),
    ("fay", "finance_analyst", "Fay（财务分析师）"),
)

DEFAULT_DEMO_PASSWORD = "meridian-demo"        # 演示口令; 用 KB_DEMO_PASSWORD 覆盖


# ---- base64url 辅助 ----

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ---- 口令哈希 ----

def hash_password(password: str, salt: bytes | None = None,
                  iterations: int = PBKDF2_ITERATIONS) -> str:
    """生成 `pbkdf2_sha256$<iterations>$<salt>$<hash>` 形式的口令摘要。"""
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64u(salt)}${_b64u(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    """恒定时间比较口令摘要; 任何格式异常一律判失败。"""
    try:
        algo, iterations, salt_b64, hash_b64 = str(encoded).split("$")
        if algo != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64u_decode(salt_b64), int(iterations),
        )
        return hmac.compare_digest(derived, _b64u_decode(hash_b64))
    except Exception:
        return False


# ---- 账号 ----

@dataclass(frozen=True)
class Account:
    """一个可登录账号: 用户名 + 角色 + 口令摘要。"""
    username: str
    role: str
    display: str
    password_hash: str


class UserDirectory:
    """账号目录 + 令牌签发/校验。"""

    def __init__(self, accounts: dict[str, Account], secret: bytes,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.accounts = accounts
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self._revoked: set[str] = set()          # 已吊销的 jti (登出)

    # ---- 登录 ----

    def authenticate(self, username: str, password: str) -> Account | None:
        """校验账号口令; 用户不存在或口令错误一律返回 None (不区分, 防用户枚举)。"""
        acct = self.accounts.get(str(username or "").strip())
        if acct is None:
            # 即使用户不存在也跑一次同等开销的哈希, 避免响应时间泄露账号是否存在
            verify_password(str(password or ""), hash_password("__nonexistent__"))
            return None
        if not verify_password(str(password or ""), acct.password_hash):
            return None
        return acct

    # ---- 令牌 ----

    def issue_token(self, acct: Account) -> tuple[str, int]:
        """签发令牌, 返回 (token, 过期时间 unix 秒)。"""
        now = int(time.time())
        exp = now + self.ttl_seconds
        payload = {
            "u": acct.username,
            "r": acct.role,
            "iat": now,
            "exp": exp,
            "jti": secrets.token_hex(12),
        }
        body = _b64u(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
        return f"{TOKEN_VERSION}.{body}.{self._sign(body)}", exp

    def verify_token(self, token: str | None) -> Account | None:
        """校验令牌: 版本/签名/过期/吊销/角色是否仍有效, 全部通过才返回账号。"""
        if not token:
            return None
        parts = str(token).strip().split(".")
        if len(parts) != 3 or parts[0] != TOKEN_VERSION:
            return None
        _version, body, signature = parts
        if not hmac.compare_digest(signature, self._sign(body)):
            return None                            # 签名不符: 篡改或密钥不对
        try:
            payload: dict[str, Any] = json.loads(_b64u_decode(body).decode("utf-8"))
        except Exception:
            return None
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None                            # 已过期
        jti = str(payload.get("jti", ""))
        if jti and jti in self._revoked:
            return None                            # 已登出
        acct = self.accounts.get(str(payload.get("u", "")))
        if acct is None or acct.role != str(payload.get("r", "")):
            return None                            # 账号不存在或角色已被变更 (令牌作废)
        return acct

    def revoke(self, token: str | None) -> bool:
        """吊销令牌 (登出); 返回是否成功解析出 jti。"""
        if not token:
            return False
        parts = str(token).strip().split(".")
        if len(parts) != 3:
            return False
        try:
            payload = json.loads(_b64u_decode(parts[1]).decode("utf-8"))
        except Exception:
            return False
        jti = str(payload.get("jti", ""))
        if not jti:
            return False
        self._revoked.add(jti)
        return True

    # ---- 内部 ----

    def _sign(self, body: str) -> str:
        return _b64u(hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).digest())


# ---- 构造 ----

def load_directory_from_env() -> tuple[UserDirectory, bool]:
    """按环境变量构造账号目录。

    返回 (directory, secret_is_ephemeral): 后者为 True 表示密钥是本次进程随机
    生成的——服务重启后所有已签发令牌立即失效, 生产必须设置 KB_AUTH_SECRET。

    环境变量:
      KB_AUTH_SECRET    令牌签名密钥 (生产必填, 建议 32 字节以上随机串)
      KB_AUTH_TTL       令牌有效期秒数, 默认 28800 (8 小时)
      KB_DEMO_PASSWORD  内置演示账号的统一口令, 默认 meridian-demo
    """
    raw_secret = os.getenv("KB_AUTH_SECRET", "").strip()
    if raw_secret:
        secret, ephemeral = raw_secret.encode("utf-8"), False
    else:
        secret, ephemeral = secrets.token_bytes(32), True

    ttl = int(os.getenv("KB_AUTH_TTL", str(DEFAULT_TTL_SECONDS)) or DEFAULT_TTL_SECONDS)
    demo_password = os.getenv("KB_DEMO_PASSWORD") or DEFAULT_DEMO_PASSWORD

    accounts: dict[str, Account] = {}
    for username, role, display in DEMO_ACCOUNTS:
        accounts[username] = Account(
            username=username, role=role, display=display,
            password_hash=hash_password(demo_password),
        )
    return UserDirectory(accounts, secret, ttl), ephemeral
