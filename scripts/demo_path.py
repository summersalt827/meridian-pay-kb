#!/usr/bin/env python3
"""Demo-path end-to-end proof: one command walks the full judge-facing flow.

Usage:
    python3 scripts/demo_path.py [base_url] [--port 8000] [--no-start]

If no server responds on /api/health, web_ui.py is started in the background
(with a fixed KB_AUTH_SECRET) and torn down at the end.

Checks (order = demo order):
  1. Health
  2. Login as all three roles (real credentials)
  3. admin asks about salary bands            -> hits internal/roadmap.md
  4. sales asks the same question             -> no_context (RBAC block)
  5. sales asks about discount tiers          -> hits gtm/pricing.md
  6. viewer asks about discount tiers         -> no_context (RBAC block)
  7. admin runs gtm_research briefing         -> multi-hop + ICP routing
  8. admin reads audit trail                  -> 200 with entries
  9. sales reads audit trail                  -> 403
 10. Ask without a token                      -> 401

Exit code 0 = all passed.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:8000"
DEMO_PASSWORD = "meridian-demo"
ACCOUNTS = {"admin": "alice", "sales": "sam", "viewer": "bob"}


def request(method, base, path, body=None, token=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def health_up(base):
    try:
        return request("GET", base, "/api/health")[0] == 200
    except Exception:
        return False


def wait_health(base, tries=40):
    for _ in range(tries):
        if health_up(base):
            return True
        time.sleep(0.5)
    return False


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def main():
    args = sys.argv[1:]
    base = DEFAULT_BASE
    no_start = "--no-start" in args
    port = "8000"
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = args[i + 1]
            base = f"http://localhost:{port}"
        elif a.startswith("http"):
            base = a
    if port == "8000" and base != DEFAULT_BASE:
        pass

    server = None
    if not health_up(base) and not no_start:
        print(f"服务未运行，启动 web_ui.py（端口 {port}）…")
        env = dict(os.environ, KB_AUTH_SECRET="demo-path-e2e-secret")
        server = subprocess.Popen(
            [sys.executable, "web_ui.py"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_health(base):
            print("❌ 服务启动失败"); return 1

    results = []
    try:
        results.append(check("1. 服务健康", health_up(base)))

        tokens = {}
        for role, username in ACCOUNTS.items():
            status, data = request("POST", base, "/api/login",
                                   {"username": username, "password": DEMO_PASSWORD})
            ok = status == 200 and bool(data.get("token"))
            tokens[role] = data.get("token")
            results.append(check(f"2. 登录 {username} ({role})", ok,
                                 f"HTTP {status}" + ("" if ok else f" {data.get('error','')}")))
            if not ok:
                return 1

        status, data = request("POST", base, "/api/ask",
                               {"question": "薪酬带宽是多少？", "agent": False}, token=tokens["admin"])
        srcs = [s.get("path", "") if isinstance(s, dict) else str(s)
                for s in (data.get("sources") or [])]
        hit_internal = any("internal" in s for s in srcs)
        results.append(check("3. admin 问薪酬 → 命中 internal",
                             status == 200 and hit_internal,
                             f"mode={data.get('mode')} sources={srcs[:3]}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "薪酬带宽是多少？", "agent": False}, token=tokens["sales"])
        results.append(check("4. sales 问同一薪酬问题 → no_context（RBAC 拦截）",
                             status == 200 and data.get("mode") == "no_context",
                             f"mode={data.get('mode')}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "大客户报价折扣能批到几折？", "agent": False}, token=tokens["sales"])
        srcs = [s.get("path", "") if isinstance(s, dict) else str(s)
                for s in (data.get("sources") or [])]
        hit_gtm = any("gtm/" in s for s in srcs)
        results.append(check("5. sales 问折扣 → 命中 gtm/pricing",
                             status == 200 and hit_gtm,
                             f"mode={data.get('mode')} sources={srcs[:3]}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "大客户报价折扣能批到几折？", "agent": False}, token=tokens["viewer"])
        results.append(check("6. viewer 问折扣 → no_context（RBAC 拦截）",
                             status == 200 and data.get("mode") == "no_context",
                             f"mode={data.get('mode')}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "计划拓展东南亚，帮我做账户情报分析并给路由建议",
                                "agent": True, "workflow": "gtm_research"}, token=tokens["admin"])
        ans = data.get("answer") or ""
        subs = data.get("sub_queries") or []
        has_brief = "需人工决策" in ans and "ICP" in ans
        results.append(check("7. admin 跑 gtm_research 账户情报（多跳 + ICP 路由）",
                             status == 200 and len(subs) > 1 and data.get("mode") != "no_context",
                             f"子问题{len(subs)}个 mode={data.get('mode')} "
                             f"决策清单={'✓' if has_brief else '（extractive 模式跳过）'}"))

        status, data = request("GET", base, "/api/audit?limit=20", token=tokens["admin"])
        records = data.get("records") or []
        results.append(check("8. admin 读审计 → 200 有记录",
                             status == 200 and len(records) > 0,
                             f"{len(records)} 条"))

        status, _ = request("GET", base, "/api/audit?limit=20", token=tokens["sales"])
        results.append(check("9. sales 读审计 → 403（治理）", status == 403, f"HTTP {status}"))

        status, _ = request("POST", base, "/api/ask", {"question": "薪酬带宽是多少？"})
        results.append(check("10. 无令牌提问 → 401", status == 401, f"HTTP {status}"))

    finally:
        if server is not None:
            server.terminate()

    passed = sum(results)
    total = len(results)
    print(f"\n== demo 路径: {passed}/{total} 通过 {'✅' if passed == total else '❌'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
