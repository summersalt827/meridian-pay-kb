#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
身份认证与越权防护验收 (修复「角色由请求体声明」漏洞的回归测试)
================================================================
修复前: role 从 POST 请求体读取, 服务端只校验它是不是 admin/sales/viewer 之一。
任何人发一条 {"role":"admin"} 即可读取全部 internal 文档。

本文件自行拉起一个独立端口的服务 (token 模式), 逐条验证攻击面, 跑完自动关闭。
不依赖外部已运行的服务, 也不需要网络之外的任何东西。

断言清单:
  1. 未登录访问 /api/ask              -> 401
  2. 请求体自称 admin (无令牌)         -> 401   ★原漏洞直接复现
  3. 错误 / 空口令                    -> 401
  4. 正确口令                         -> 200 且角色由服务端账号决定
  5. 已登录 viewer 伪造 role=admin      -> 仍按 viewer 处理, 拿不到 internal
  6. 已登录 viewer 伪造 username        -> 审计记录仍是真实用户
  7. 令牌篡改 / 伪造签名               -> 401
  8. 登出后令牌立即失效                -> 401
  9. 审计接口: 无令牌 401 / viewer 403 / admin 200
 10. 旧越权入口 ?role=admin (无令牌)    -> 401   ★原漏洞直接复现
 11. 令牌里改角色字段 (签名失效)        -> 401

运行: python3 test_auth.py   (退出码 0=通过, 1=有失败)
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("KB_AUTH_TEST_PORT", "8765"))
BASE = f"http://localhost:{PORT}"
SECRET = "test-secret-do-not-use-in-production"
DEMO_PASSWORD = os.getenv("KB_DEMO_PASSWORD", "meridian-demo")


def call(method: str, path: str, body: dict | None = None, token: str | None = None,
         raw_token: str | None = None) -> tuple[int, dict]:
    """发一次 HTTP 请求; raw_token 用于构造畸形/伪造令牌。"""
    headers = {"Content-Type": "application/json"}
    if raw_token:
        headers["Authorization"] = "Bearer " + raw_token
    elif token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def login(username: str, password: str) -> tuple[int, str, dict]:
    status, data = call("POST", "/api/login", {"username": username, "password": password})
    return status, data.get("token", ""), data


def wait_ready(proc: subprocess.Popen, timeout: float = 30.0) -> bool:
    """轮询 /api/health 直到服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def forged_role_token(token: str, fake_role: str) -> str:
    """把令牌 payload 里的角色改掉但保留原签名——必然验签失败。"""
    try:
        _v, body, sig = token.split(".")
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode())
        payload["r"] = fake_role
        new_body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        return f"v1.{new_body}.{sig}"
    except Exception:
        return "v1.broken.sig"


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="kb_auth_test_")
    db_path = os.path.join(tmpdir, "audit.db")
    env = dict(os.environ)
    env.update({
        "KB_AUTH_SECRET": SECRET,
        "KB_AUTH_MODE": "token",
        "WEB_PORT": str(PORT),
        "KB_DB": db_path,
        "KB_ROOT": os.path.join(BASE_DIR, "sample_docs"),
    })

    proc = subprocess.Popen(
        [sys.executable, "web_ui.py"], cwd=BASE_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_ready(proc):
            print("❌ 服务启动失败")
            return 1

        failures: list[str] = []
        passed = 0
        total = 0

        def check(label: str, ok: bool, detail: str) -> None:
            nonlocal passed, total
            total += 1
            if ok:
                passed += 1
                print(f"  ✅ {label}: {detail}")
            else:
                failures.append(f"{label}: {detail}")
                print(f"  ❌ {label}: {detail}")

        print("== 一、未登录一律拒绝 ==")
        s, d = call("POST", "/api/ask", {"question": "薪酬带宽是多少？"})
        check("未登录提问", s == 401, f"HTTP {s}")

        s, d = call("POST", "/api/ask",
                    {"question": "薪酬带宽是多少？", "role": "admin", "username": "attacker"})
        check("★请求体自称 admin (原漏洞)", s == 401, f"HTTP {s} —— 角色声明不再被采信")

        s, d = call("GET", "/api/audit?limit=3&role=admin")
        check("★旧越权入口 ?role=admin (原漏洞)", s == 401, f"HTTP {s}")

        print("\n== 二、登录 ==")
        s, _, d = login("alice", "wrong-password")
        check("错误口令", s == 401, f"HTTP {s} {d.get('error','')}")
        s, _, d = login("alice", "")
        check("空口令", s == 400, f"HTTP {s} {d.get('error','')}")
        s, _, d = login("nobody", DEMO_PASSWORD)
        check("不存在的用户", s == 401, f"HTTP {s} {d.get('error','')}")

        s, tok_admin, d = login("alice", DEMO_PASSWORD)
        check("admin 登录", s == 200 and d.get("role") == "admin",
              f"HTTP {s} role={d.get('role')} 目录={d.get('allowed_dirs')}")
        s, tok_viewer, d = login("bob", DEMO_PASSWORD)
        check("viewer 登录", s == 200 and d.get("role") == "viewer",
              f"HTTP {s} role={d.get('role')} 目录={d.get('allowed_dirs')}")
        s, tok_sales, d = login("sam", DEMO_PASSWORD)
        check("sales 登录", s == 200 and d.get("role") == "sales",
              f"HTTP {s} role={d.get('role')} 目录={d.get('allowed_dirs')}")

        print("\n== 三、已登录后伪造身份无效 ==")
        s, d = call("POST", "/api/ask",
                    {"question": "薪酬带宽是多少？", "role": "admin"}, token=tok_viewer)
        paths = [x["path"] for x in d.get("sources", [])]
        leak = [p for p in paths if p.startswith("internal")]
        check("★viewer 伪造 role=admin",
              s == 200 and d.get("role") == "viewer" and not leak,
              f"响应角色={d.get('role')} internal泄漏={leak or '无'}")

        s, d = call("POST", "/api/ask",
                    {"question": "报销流程是什么？", "username": "alice"}, token=tok_viewer)
        check("★viewer 伪造 username", d.get("user") == "bob",
              f"审计记录用户={d.get('user')} (伪造值 alice 被忽略)")

        s, d = call("POST", "/api/ask",
                    {"question": "薪酬带宽是多少？", "role": "admin"}, token=tok_sales)
        leak = [x["path"] for x in d.get("sources", []) if x["path"].startswith("internal")]
        check("★sales 伪造 role=admin", d.get("role") == "sales" and not leak,
              f"响应角色={d.get('role')} internal泄漏={leak or '无'}")

        print("\n== 四、令牌完整性 ==")
        s, d = call("POST", "/api/ask", {"question": "报销流程是什么？"},
                    raw_token=tok_viewer[:-4] + "AAAA")
        check("篡改签名", s == 401, f"HTTP {s}")

        s, d = call("POST", "/api/ask", {"question": "报销流程是什么？"},
                    raw_token=forged_role_token(tok_viewer, "admin"))
        check("★令牌内改角色字段", s == 401, f"HTTP {s} —— 改 payload 即验签失败")

        s, d = call("POST", "/api/ask", {"question": "报销流程是什么？"},
                    raw_token="v1.notatoken.notasig")
        check("完全伪造令牌", s == 401, f"HTTP {s}")

        print("\n== 五、登出即失效 ==")
        s, d = call("POST", "/api/logout", {}, token=tok_viewer)
        check("登出接口", s == 200 and d.get("revoked") is True, f"HTTP {s} revoked={d.get('revoked')}")
        s, d = call("POST", "/api/ask", {"question": "报销流程是什么？"}, token=tok_viewer)
        check("★登出后原令牌", s == 401, f"HTTP {s}")

        print("\n== 六、审计接口权限 ==")
        s, d = call("GET", "/api/audit?limit=3")
        check("无令牌", s == 401, f"HTTP {s}")
        s, d = call("GET", "/api/audit?limit=3", token=tok_sales)
        check("sales", s == 403, f"HTTP {s}")
        s, d = call("GET", "/api/audit?limit=3", token=tok_admin)
        check("admin", s == 200, f"HTTP {s} 记录数={len(d.get('records', []))}")

        print("\n== 七、权限仍按角色生效 (未因鉴权改造而失效) ==")
        s, d = call("POST", "/api/ask", {"question": "薪酬带宽是多少？"}, token=tok_admin)
        hit = any(x["path"].startswith("internal") for x in d.get("sources", []))
        check("admin 可见 internal", s == 200 and hit, f"mode={d.get('mode')} 来源={len(d.get('sources',[]))}")
        s, d = call("POST", "/api/ask", {"question": "企业客户的分层路由规则是什么？"}, token=tok_sales)
        hit = any("gtm/" in x["path"] for x in d.get("sources", []))
        check("sales 可见 gtm", s == 200 and hit, f"mode={d.get('mode')} 来源={len(d.get('sources',[]))}")
        s, d = call("POST", "/api/ask", {"question": "报销流程是什么？"}, token=tok_viewer if False else tok_sales)
        check("sales 可见 public", s == 200 and d.get("mode") != "no_context", f"mode={d.get('mode')}")

        print(f"\n== 结果: {passed}/{total} 通过")
        if failures:
            print("失败明细:")
            for f in failures:
                print("  ❌ " + f)
            return 1
        print("== 全部通过 ✅")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
