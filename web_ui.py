#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识库问答 Web 界面 / Agent 工作流网关 (KB-QA Web UI)
======================================================
在 kb_qa.py 之上加一层本地 Web 聊天界面与 HTTP API, 复用其摄入/检索/RBAC/审计能力,
不改动 kb_qa.py 的任何核心逻辑。

角色与目录权限 (viewer < sales < admin):
  - admin : 不限目录, 且可查看审计日志
  - sales : allowed_dirs=("public", "gtm")
  - viewer: allowed_dirs=("public",)

特性:
  1. 仅用标准库 http.server 实现, 不引入 Flask/FastAPI 等第三方依赖
  2. GET  /             -> 返回 static/index.html 单页聊天界面
  3. GET  /api/health   -> 返回服务与索引状态 (分块数/文档数/可用工作流/鉴权模式)
  4. POST /api/login    -> {username, password} => {token, user, role, expires_at}
                           口令经 PBKDF2 校验, 令牌由服务端 HMAC 签名
  5. POST /api/ask      -> Authorization: Bearer <token>; body {question, agent?, workflow?}
                           => {answer, mode, sources, ts, agent, workflow, sub_queries, elapsed_ms}
  6. GET  /api/audit    -> 需 Bearer 令牌且角色为 admin, 否则 401/403
  7. 相关性门 _gate_sources(): 低于 min_score 的来源一律过滤, 全部过滤则判 no_context
  8. 启动时自动 kb.build(), 静态资源禁止路径穿越
  9. 部署前置 (10/12): 默认绑定 0.0.0.0, 端口读 PORT / WEB_PORT 环境变量 (Render 部署用)

鉴权 (修复越权漏洞)
------------------------
修复前: role 由 POST 请求体声明, 服务端只校验它是不是 admin/sales/viewer 之一。
任何人发一条 {"role":"admin"} 就能读到全部 internal 文档——角色是客户端说了算的。

修复后:
  - 身份一律来自服务端签发的令牌, 请求体/查询串里的 role 与 username 一律忽略;
  - 「角色 -> 目录」映射仍在服务端硬编码, 令牌只携带角色名, 目录永不可被客户端影响;
  - 无令牌/令牌过期/签名不符 -> 401; 角色不足 -> 403;
  - 生产请设置 KB_AUTH_SECRET (否则密钥随机生成, 进程重启即全部登出)。

保留一个显式降级开关 KB_AUTH_MODE=anonymous (仅本地演示, 启动会打印醒目警告):
该模式下恢复「请求体声明角色」的旧行为, 便于对照回归。

依赖: numpy (kb_qa 需要), 其余全部标准库。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# 保证从任意工作目录启动都能导入同目录下的 kb_qa
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from auth import (  # noqa: E402
    DEMO_ACCOUNTS,
    Account,
    UserDirectory,
    load_directory_from_env,
)
from kb_qa import (  # noqa: E402  (需先补 sys.path)
    DEFAULT_WORKFLOW_ID,
    WORKFLOWS,
    Config,
    KnowledgeBase,
    User,
)

# ============================================================
# 常量
# ============================================================

VALID_ROLES: tuple[str, ...] = ("admin", "sales", "viewer")

# 角色 -> 可检索目录 (相对 kb_root); 空元组表示不限制, 由 User.can_access 放行全部
ROLE_ALLOWED_DIRS: dict[str, tuple[str, ...]] = {
    "admin": (),
    "sales": ("public", "gtm"),
    "viewer": ("public",),
    "compliance_officer": ("public", "compliance"),
    "risk_analyst": ("public", "compliance", "risk"),
    "card_ops": ("public", "card_ops"),
    "finance_analyst": ("public", "finance"),
}

# 可用工作流 (来自 kb_qa.WORKFLOWS 注册表, 这里只做接口层的白名单)
VALID_WORKFLOWS: tuple[str, ...] = tuple(WORKFLOWS.keys())

# 与 kb_qa 的 min_score 语义一致: 低于该相关性分数的来源视为噪声, 过滤后无剩余则判无命中
NO_CONTEXT_TEXT = "未检索到相关文档, 无法回答。"

# ============================================================
# 配置
# ============================================================

@dataclass
class WebConfig:
    """Web 服务配置。"""
    host: str = "0.0.0.0"                                                  # 监听地址
    port: int = 8000                                                       # 监听端口
    kb_root: str = field(default_factory=lambda: os.path.join(BASE_DIR, "sample_docs"))
    db_path: str = field(default_factory=lambda: os.path.join(BASE_DIR, "kb_audit.db"))
    static_dir: str = field(default_factory=lambda: os.path.join(BASE_DIR, "static"))
    top_k: int = 4                                                         # 返回来源数
    max_question_len: int = 2000                                           # 单条问题最大字符数
    audit_default_limit: int = 20                                          # 审计日志默认条数
    audit_max_limit: int = 100                                             # 审计日志条数上限
    min_score: float = 0.17                                                # 来源相关性下限 (与 kb_qa 对齐)
    default_agent: bool = True                                             # /api/ask 未传 agent 时的默认行为
    # 鉴权模式: token=需要登录 (默认, 生产必开); anonymous=关闭鉴权, 仅本地演示
    auth_mode: str = "token"
    # 请求限流 (公开 live URL 保护): 问答/反馈 每身份每分钟上限; 登录尝试按 IP 限流防爆破
    rate_limit_per_min: int = 60
    login_rate_limit_per_min: int = 10

    @classmethod
    def from_env(cls) -> "WebConfig":
        """从环境变量读取, 未设置则用默认值。

        端口读取顺序: PORT (Render 部署标准变量) -> WEB_PORT -> 8000。
        """
        mode = (os.getenv("KB_AUTH_MODE") or "token").strip().lower()
        return cls(
            host=os.getenv("WEB_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT") or os.getenv("WEB_PORT") or "8000"),
            kb_root=os.getenv("KB_ROOT") or os.path.join(BASE_DIR, "sample_docs"),
            db_path=os.getenv("KB_DB") or os.path.join(BASE_DIR, "kb_audit.db"),
            top_k=int(os.getenv("KB_TOP_K", "4")),
            min_score=float(os.getenv("KB_MIN_SCORE", "0.17")),
            default_agent=_env_bool("KB_AGENT_DEFAULT", True),
            auth_mode="anonymous" if mode == "anonymous" else "token",
            rate_limit_per_min=int(os.getenv("KB_RATE_LIMIT", "60")),
            login_rate_limit_per_min=int(os.getenv("KB_LOGIN_RATE_LIMIT", "10")),
        )

    def to_kb_config(self) -> Config:
        """转换为 kb_qa.Config: 先用 from_env 透传 LLM/向量变量, 再覆盖本地路径。

        注意: min_score 由 web 配置同步到检索层, KB_MIN_SCORE 单一数据源
        同时驱动 retriever 阈值与 web 门 (幂等双保险)。
        """
        cfg = Config.from_env()
        return replace(cfg, kb_root=self.kb_root, db_path=self.db_path,
                       top_k=self.top_k, min_score=self.min_score)


class RateLimiter:
    """滑动窗口限流 (内存级, 线程安全; 公开 live URL 的自我保护)。"""

    def __init__(self, window: float = 60.0):
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _env_bool(name: str, default: bool) -> bool:
    """环境变量布尔解析: 1/true/yes/on 为真, 0/false/no/off 为假。"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _as_bool(value: Any, default: bool) -> bool:
    """请求体里的布尔解析, 兼容 true/false 字符串与 0/1。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _now_iso() -> str:
    """本地时区 ISO8601 时间戳。"""
    import datetime
    return datetime.datetime.now().astimezone().isoformat()

# ============================================================
# HTTP 服务器与请求处理
# ============================================================

class KBQAHTTPServer(ThreadingHTTPServer):
    """持有知识库句柄的 HTTP 服务器 (多线程, 支持并发提问)。"""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 kb: KnowledgeBase, web_cfg: WebConfig,
                 directory: UserDirectory | None = None):
        super().__init__(addr, handler)
        self.kb = kb            # 全局共享的知识库实例 (索引只读, 线程安全)
        self.web_cfg = web_cfg
        self.directory = directory   # 账号目录 (鉴权关闭时为 None)
        self.limiter = RateLimiter()


class KBHandler(BaseHTTPRequestHandler):
    """路由: 静态页面 + /api/health + /api/login + /api/ask + /api/audit。"""

    server_version = "KBQAWeb/1.0"

    # ---- 便捷访问 ----

    @property
    def kb(self) -> KnowledgeBase:
        return self.server.kb  # type: ignore[attr-defined]

    @property
    def web_cfg(self) -> WebConfig:
        return self.server.web_cfg  # type: ignore[attr-defined]

    @property
    def directory(self) -> UserDirectory | None:
        return self.server.directory  # type: ignore[attr-defined]

    # ---- 鉴权 ----

    def _auth_enabled(self) -> bool:
        return self.web_cfg.auth_mode == "token" and self.directory is not None

    def _bearer_token(self) -> str | None:
        """从 Authorization: Bearer <token> 提取令牌。"""
        raw = self.headers.get("Authorization") or ""
        parts = raw.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        return None

    def _current_account(self) -> Account | None:
        """当前请求身份; 鉴权关闭时返回 None (由调用方走匿名分支)。"""
        if not self._auth_enabled():
            return None
        return self.directory.verify_token(self._bearer_token())  # type: ignore[union-attr]

    def _require_account(self) -> Account | None:
        """要求已登录; 未通过时直接写 401 并返回 None。"""
        acct = self._current_account()
        if acct is None:
            self._send_error_json(401, "未登录或令牌已失效, 请先 POST /api/login")
            return None
        return acct

    # ---- 基础 IO ----

    def _send(self, status: int, body: bytes, content_type: str,
              extra_headers: dict[str, str] | None = None) -> None:
        """统一的响应写出。"""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 允许本地其它端口的页面/脚本调试
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message, "status": status, "ts": _now_iso()})

    def _send_file(self, abs_path: str) -> None:
        """发送静态文件, 按扩展名猜测 MIME。"""
        try:
            data = Path(abs_path).read_bytes()
        except OSError:
            self._send_error_json(404, "静态资源不存在")
            return
        ctype, _ = mimetypes.guess_type(abs_path)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def _read_json_body(self) -> dict[str, Any]:
        """读取并解析 JSON 请求体, 非法输入抛 ValueError。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("请求体为空")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"JSON 解析失败: {e}")
        if not isinstance(obj, dict):
            raise ValueError("JSON 顶层必须是对象")
        return obj

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 约定命名)
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html", "/chat"):
            self._send_file(os.path.join(self.web_cfg.static_dir, "index.html"))
        elif path == "/api/health":
            self._handle_health()
        elif path == "/api/audit":
            self._handle_audit(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
        elif path.startswith("/static/"):
            self._handle_static(path[len("/static/"):])
        else:
            self._send_error_json(404, f"未知路径: {path}")

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/login":
            self._handle_login()
        elif path == "/api/logout":
            self._handle_logout()
        elif path == "/api/ask":
            self._handle_ask()
        elif path == "/api/feedback":
            self._handle_feedback()
        else:
            self._send_error_json(404, f"未知路径: {path}")

    def do_OPTIONS(self) -> None:  # noqa: N802
        """预检请求, 便于本地跨域调试。"""
        self._send(204, b"", "text/plain; charset=utf-8")

    def _handle_static(self, rel: str) -> None:
        """提供 static/ 下的静态资源, 禁止 ../ 路径穿越。"""
        rel = urllib.parse.unquote(rel)
        if not rel or rel.endswith("/"):
            self._send_error_json(404, "静态资源不存在")
            return
        root = os.path.abspath(self.web_cfg.static_dir)
        target = os.path.abspath(os.path.join(root, rel))
        if target != root and not target.startswith(root + os.sep):
            self._send_error_json(403, "禁止访问该路径")
            return
        if not os.path.isfile(target):
            self._send_error_json(404, "静态资源不存在")
            return
        self._send_file(target)

    def _handle_health(self) -> None:
        """服务与索引状态。"""
        kb = self.kb
        docs = sorted({c.path for c in kb.chunks})
        self._send_json(200, {
            "status": "ok",
            "chunks": len(kb.chunks),
            "documents": len(docs),
            "kb_root": self.web_cfg.kb_root,
            "top_k": self.web_cfg.top_k,
            "roles": list(VALID_ROLES),
            "role_dirs": {r: list(d) for r, d in ROLE_ALLOWED_DIRS.items()},
            "workflows": list(VALID_WORKFLOWS),                              # 可用工作流
            "workflow_names": {w.id: w.name for w in WORKFLOWS.values()},    # 展示名映射
            "auth_mode": "token" if self._auth_enabled() else "anonymous",   # 是否需要登录
            "demo_accounts": sorted(self.directory.accounts) if self.directory else [],
            "ts": _now_iso(),
        })

    # ---- 登录 / 登出 ----

    def _handle_login(self) -> None:
        """口令换令牌: POST {username, password} => {token, user, role, expires_at}。"""
        try:
            payload = self._read_json_body()
        except ValueError as e:
            self._send_error_json(400, str(e))
            return

        if not self._auth_enabled():
            self._send_error_json(400, "当前为 anonymous 模式, 无需登录")
            return

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        if not username or not password:
            self._send_error_json(400, "username 与 password 均不能为空")
            return

        # 登录按 IP 限流: PBKDF2 200k 迭代成本高, 防公开环境口令爆破
        if not self.server.limiter.allow(  # type: ignore[attr-defined]
                f"login:{self.client_address[0]}", self.web_cfg.login_rate_limit_per_min):
            self._send_error_json(429, "登录尝试过于频繁, 请稍后再试")
            return

        directory = self.directory
        assert directory is not None
        acct = directory.authenticate(username, password)
        if acct is None:
            # 不区分「用户不存在」与「口令错误」, 避免账号枚举
            self._send_error_json(401, "用户名或口令不正确")
            return

        token, expires_at = directory.issue_token(acct)
        self._send_json(200, {
            "status": "ok",
            "token": token,
            "user": acct.username,
            "display": acct.display,
            "role": acct.role,                       # 角色由服务端账号决定, 与请求无关
            "allowed_dirs": list(ROLE_ALLOWED_DIRS.get(acct.role, ())),
            "ttl_seconds": directory.ttl_seconds,
            "expires_at": expires_at,
            "ts": _now_iso(),
        })

    def _handle_logout(self) -> None:
        """吊销当前令牌 (加入 jti 黑名单, 立即失效)。"""
        if not self._auth_enabled():
            self._send_json(200, {"status": "ok", "revoked": False})
            return
        directory = self.directory
        assert directory is not None
        revoked = directory.revoke(self._bearer_token())
        self._send_json(200, {"status": "ok", "revoked": revoked})

    def _handle_feedback(self) -> None:
        """点赞/点踩: 任何已登录角色可反馈, 写入 SQLite 反馈表 (运营闭环)。"""
        try:
            payload = self._read_json_body()
        except ValueError as e:
            self._send_error_json(400, str(e))
            return

        if self._auth_enabled():
            acct = self._require_account()
            if acct is None:
                return                                   # 已写 401
            username = acct.username
        else:
            username = str(payload.get("username", "")).strip() or "anonymous"

        question = str(payload.get("question", "")).strip()[:500]
        if not question:
            self._send_error_json(400, "question 不能为空")
            return
        try:
            vote = int(payload.get("vote", 0))
        except (TypeError, ValueError):
            self._send_error_json(400, "vote 必须是 1 或 -1")
            return
        if vote not in (1, -1):
            self._send_error_json(400, "vote 只能是 1 或 -1")
            return

        user = User(username=username, role="viewer", allowed_dirs=())
        self.kb.audit.log_feedback(user, question, vote)
        self._send_json(200, {
            "status": "ok",
            "vote": vote,
            "stats": self.kb.audit.feedback_stats(),
            "ts": _now_iso(),
        })

    def _handle_audit(self, query: dict[str, list[str]]) -> None:
        """审计日志接口: 仅 admin 可见, 返回最近问答记录 (时间倒序)。

        角色判定来自令牌而非查询串——修复前 ?role=admin 即可越权读取全部审计记录。
        """
        if self._auth_enabled():
            acct = self._require_account()
            if acct is None:
                return                                   # 已写 401
            if acct.role != "admin":
                self._send_error_json(403, "审计日志仅 admin 可见")
                return
            role = acct.role
        else:
            # anonymous 模式: 保留旧的查询串声明方式, 仅供本地演示
            role = (query.get("role") or [""])[0].strip().lower()
            if role != "admin":
                self._send_error_json(403, "审计日志仅 admin 可见")
                return

        raw_limit = (query.get("limit") or [str(self.web_cfg.audit_default_limit)])[0].strip()
        try:
            limit = int(raw_limit)
        except ValueError:
            self._send_error_json(400, f"limit 必须是整数: {raw_limit}")
            return
        limit = max(1, min(limit, self.web_cfg.audit_max_limit))

        try:
            rows = self.kb.audit.recent(limit)
        except Exception as e:
            self._send_error_json(500, f"读取审计日志失败: {e}")
            return

        records: list[dict[str, Any]] = []
        for r in rows:
            sources = self._parse_source_docs(r.get("source_docs", ""))
            records.append({
                "ts": r.get("ts", ""),
                "user": r.get("user", ""),
                "role": r.get("role", ""),
                "question": r.get("question", ""),
                "answer": r.get("answer", ""),
                "answer_mode": r.get("answer_mode", ""),
                "source_docs": r.get("source_docs", ""),   # 保留原始 JSON 字符串
                "source_count": len(sources),              # 前端表格只需条数
            })

        self._send_json(200, {
            "status": "ok",
            "limit": limit,
            "count": len(records),
            "records": records,
            "stats": self.kb.audit.stats(),             # 可观测聚合 (总量/模式/用户/top 文档)
            "feedback": self.kb.audit.feedback_stats(),  # 反馈闭环聚合
            "ts": _now_iso(),
        })

    @staticmethod
    def _parse_source_docs(raw: str) -> list[dict[str, Any]]:
        """解析审计日志里的 source_docs; 解析失败时退化为空列表。"""
        if not raw:
            return []
        try:
            obj = json.loads(raw)
        except Exception:
            return []
        return obj if isinstance(obj, list) else []

    def _handle_ask(self) -> None:
        """问答接口: 构造 User -> kb.ask() / kb.ask_agent() -> 返回结构与来源。

        - agent=true (缺省): 走参数化多跳 agent, workflow 决定规划/合成 prompt
        - agent=false       : 走原单跳 ask(), 用于回归测试, 行为与改造前完全一致
        """
        try:
            payload = self._read_json_body()
        except ValueError as e:
            self._send_error_json(400, str(e))
            return

        question = str(payload.get("question", "")).strip()
        use_agent = _as_bool(payload.get("agent"), self.web_cfg.default_agent)
        workflow_id = str(payload.get("workflow", DEFAULT_WORKFLOW_ID)).strip() or DEFAULT_WORKFLOW_ID

        if not question:
            self._send_error_json(400, "question 不能为空")
            return
        if len(question) > self.web_cfg.max_question_len:
            self._send_error_json(400, f"question 超过 {self.web_cfg.max_question_len} 字符")
            return
        # agent=false 时忽略 workflow (不校验), 保证回归路径不受影响
        if use_agent and workflow_id not in VALID_WORKFLOWS:
            self._send_error_json(400, "workflow 只能是 " + " / ".join(VALID_WORKFLOWS))
            return

        # ---- 身份: 一律以服务端为准 ----
        # 修复前 username/role 直接取自请求体, 任何人都能声明自己是 admin。
        # 现在请求体里的这两个字段被完全忽略, 只接受令牌里的身份。
        if self._auth_enabled():
            acct = self._require_account()
            if acct is None:
                return                                   # 已写 401
            username, role = acct.username, acct.role
        else:
            # anonymous 模式: 沿用旧的请求体声明方式, 仅供本地演示
            username = str(payload.get("username", "")).strip() or "admin"
            role = str(payload.get("role", "admin")).strip().lower() or "admin"
            if role not in VALID_ROLES:
                self._send_error_json(400, "role 只能是 " + " / ".join(VALID_ROLES))
                return

        # 目录白名单由服务端按角色硬编码, 不接受任何客户端输入
        allowed_dirs: tuple[str, ...] = ROLE_ALLOWED_DIRS[role]
        user = User(username=username, role=role, allowed_dirs=allowed_dirs)

        # 问答按身份限流 (登录态=账号, 匿名=IP): 保护公开 live URL 不被刷爆
        rl_key = f"ask:{username}" if self._auth_enabled() else f"ask_ip:{self.client_address[0]}"
        if not self.server.limiter.allow(rl_key, self.web_cfg.rate_limit_per_min):  # type: ignore[attr-defined]
            self._send_error_json(429, "请求过于频繁, 请稍后再试")
            return

        t0 = time.perf_counter()
        try:
            if use_agent:
                ans = self.kb.ask_agent(question, user, workflow_id=workflow_id,
                                        top_k=self.web_cfg.top_k)
            else:
                ans = self.kb.ask(question, user, top_k=self.web_cfg.top_k)
        except Exception as e:  # 检索/生成异常不打断服务
            self._send_error_json(500, f"问答失败: {e}")
            return
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # 相关性门: 过滤低于 min_score 的来源; 若全部被过滤则判为无命中
        # (agent 模式的合并来源同样要过这道门)
        sources = self._gate_sources(ans.sources, self.web_cfg.min_score)
        mode = ans.mode if sources else "no_context"
        sub_queries = [str(q) for q in (getattr(ans, "sub_queries", None) or [])]
        agent_effective = bool(use_agent) and len(sub_queries) > 1   # 子问题 >1 才算多跳生效

        self._send_json(200, {
            "question": ans.question,
            "answer": ans.answer if sources else NO_CONTEXT_TEXT,
            "mode": mode,
            "sources": [
                {
                    "path": s["path"],
                    "title": s["title"],
                    "score": round(float(s["score"]), 3),        # 前端展示 3 位小数
                    "source": s.get("source") or "Confluence",   # 来源平台 (Confluence/Drive/Jira)
                }
                for s in sources
            ],
            "user": ans.user,
            "role": role,
            "agent": agent_effective,            # 本次是否多跳生效 (子问题数 > 1)
            "agent_requested": use_agent,        # 本次是否请求了 agent 模式
            "agent_effective": agent_effective,  # 同上, 兼容字段
            "workflow": workflow_id if use_agent else DEFAULT_WORKFLOW_ID,
            "sub_queries": sub_queries,          # 实际拆解出的子问题 (单跳时为 [原问题])
            "elapsed_ms": elapsed_ms,            # 全链路耗时 (含 LLM 调用), 仅做展示
            "latency_ms": elapsed_ms,            # 兼容旧字段
            "ts": ans.ts,
        })

    @staticmethod
    def _gate_sources(sources: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
        """过滤低于相关性下限的来源。

        与 kb_qa 的 min_score 语义一致 (同为 relevance 绝对分, 0~1 跨查询可比),
        因此这里的二次过滤是幂等的, 不会出现额外截断。
        """
        if min_score <= 0 or not sources:
            return sources
        return [s for s in sources if float(s.get("score", 0.0)) >= min_score]

    def log_message(self, fmt: str, *args: Any) -> None:
        """精简访问日志, 便于观察问答请求。"""
        sys.stderr.write("[%s] %s %s\n" % (
            self.log_date_time_string(), self.command, self.path,
        ))

# ============================================================
# 启动入口
# ============================================================

def build_kb(cfg: WebConfig) -> KnowledgeBase:
    """构建知识库索引 (启动时执行一次)。"""
    kb = KnowledgeBase(cfg.to_kb_config())
    kb.build()
    return kb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="知识库问答 Web 界面 / Agent 工作流网关")
    ap.add_argument("--host", default=None, help="监听地址, 默认 0.0.0.0")
    ap.add_argument("--port", type=int, default=None, help="监听端口, 默认 8000")
    ap.add_argument("--kb", default=None, help="知识库根目录 (Markdown)")
    ap.add_argument("--db", default=None, help="审计日志 SQLite 路径")
    ap.add_argument("--top-k", type=int, default=None, help="返回来源数")
    args = ap.parse_args(argv)

    cfg = WebConfig.from_env()
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.kb:
        cfg.kb_root = os.path.abspath(args.kb)
    if args.db:
        cfg.db_path = os.path.abspath(args.db)
    if args.top_k:
        cfg.top_k = args.top_k

    if not os.path.isdir(cfg.kb_root):
        print(f"[警告] 知识库目录不存在: {cfg.kb_root} (仍可启动, 但检索结果为空)")
    if not os.path.isdir(cfg.static_dir):
        print(f"[错误] 静态目录不存在: {cfg.static_dir}")
        return 2

    # ---- 鉴权 ----
    directory: UserDirectory | None = None
    if cfg.auth_mode == "token":
        directory, ephemeral = load_directory_from_env()
        print("[启动] 鉴权: token 模式 (角色由服务端签发, 请求体声明无效)")
        if ephemeral:
            print("[警告] 未设置 KB_AUTH_SECRET —— 签名密钥为本次进程随机生成, "
                  "服务重启后所有令牌立即失效。生产务必设置该环境变量。")
        demo_pwd = os.getenv("KB_DEMO_PASSWORD") or "meridian-demo"
        print("[启动] 演示账号: " + ", ".join(
            f"{u}({r})" for u, r, _d in DEMO_ACCOUNTS
        ) + f" —— 统一口令: {demo_pwd} (用 KB_DEMO_PASSWORD 修改)")
    else:
        print("[警告] 鉴权: anonymous 模式 —— 角色由请求体声明, 任何人可自称 admin。"
              "仅限本地演示, 切勿用于任何可公开访问的环境。")

    t0 = time.time()
    kb = build_kb(cfg)
    docs = sorted({c.path for c in kb.chunks})
    print(f"[启动] 知识库: {cfg.kb_root}")
    print(f"[启动] 已摄入 {len(kb.chunks)} 个分块 / {len(docs)} 篇文档, 耗时 {time.time() - t0:.2f}s")
    print(f"[启动] 审计日志: {cfg.db_path}")
    print(f"[启动] 工作流: {', '.join(VALID_WORKFLOWS)}")
    print(f"[启动] 相关性阈值 min_score = {cfg.min_score} (relevance 绝对分)")
    print(f"[启动] 服务地址: http://localhost:{cfg.port}/")

    httpd = KBQAHTTPServer((cfg.host, cfg.port), KBHandler, kb=kb, web_cfg=cfg,
                           directory=directory)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[退出] 收到中断信号, 正在关闭服务...")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
