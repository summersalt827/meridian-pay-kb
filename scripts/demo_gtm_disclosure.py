#!/usr/bin/env python3
"""GTM 披露门 end-to-end proof: 同一账号、同一问题, 「客户在场」开关前后的来源对比。

Usage:
    python3 scripts/demo_gtm_disclosure.py [base_url] [--port 8000] [--no-start]

无服务时自动以固定 KB_AUTH_SECRET 起服务并在结束时回收。
输出本身就是视频脚本 —— 录制时照着念即可。

Checks (order = demo order):
  1. 服务健康
  2. 登录 sales (sam)
  3. 内部语境: 案例+折扣 多跳 → 4 条来源 (案例/定价/异议话术/销售SOP)
  4. 内部语境来源全部带 audience 字段
  5. 客户在场: 同一问题 → 只剩 case-studies (1 条)
  6. 客户在场零泄漏: pricing/objection/playbook 全部消失
  7. sales 客户在场问折扣 → no_context (披露门)
  8. viewer 客户在场问薪酬 → no_context (RBAC 不放宽)
  9. admin 客户在场问薪酬 → no_context (披露门收窄 admin)
 10. 内部语境 gtm_research 账户情报 → 多跳简报不受影响
 11. admin 验证审计链 → 200 完整 (披露门问答已入链)
 12. viewer 验证审计链 → 403 (治理)

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
DEMO_QUESTION = "客户问我们有没有同类案例，以及能给多少折扣"


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

    server = None
    if not health_up(base) and not no_start:
        print(f"服务未运行，启动 web_ui.py（端口 {port}）…")
        env = dict(os.environ, KB_AUTH_SECRET="demo-gtm-disclosure-secret")
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

        # 3-4: 内部语境
        status, data = request("POST", base, "/api/ask",
                               {"question": DEMO_QUESTION, "agent": True},
                               token=tokens["sales"])
        srcs = [s.get("path", "") if isinstance(s, dict) else str(s)
                for s in (data.get("sources") or [])]
        want = ["gtm/case-studies.md", "gtm/pricing.md",
                "gtm/objection-playbook.md", "gtm/sales-playbook.md"]
        distinct = sorted({s for s in srcs if s in want})
        results.append(check("3. 内部语境: 案例+折扣 → 4 个不同文档 (案例/定价/异议/销售SOP)",
                             status == 200 and distinct == sorted(want),
                             f"mode={data.get('mode')} docs={distinct}"))

        has_aud = all(isinstance(s, dict) and "audience" in s for s in (data.get("sources") or []))
        results.append(check("4. 来源全部带 audience 披露分级", has_aud))

        # 5-6: 客户在场
        status, data = request("POST", base, "/api/ask",
                               {"question": DEMO_QUESTION, "agent": True,
                                "external_context": True},
                               token=tokens["sales"])
        srcs = [s.get("path", "") if isinstance(s, dict) else str(s)
                for s in (data.get("sources") or [])]
        results.append(check("5. 客户在场: 同一问题 → 只剩 case-studies",
                             status == 200 and srcs == ["gtm/case-studies.md"],
                             f"mode={data.get('mode')} sources={srcs}"))

        leak = [s for s in srcs if "pricing" in s or "objection" in s or "playbook" in s]
        results.append(check("6. 客户在场零泄漏: 定价/异议/销售SOP 全部消失",
                             not leak, f"sources={srcs}"))

        # 7-9: 披露门边界
        status, data = request("POST", base, "/api/ask",
                               {"question": "大客户报价折扣能批到几折？", "agent": False,
                                "external_context": True},
                               token=tokens["sales"])
        results.append(check("7. sales 客户在场问折扣 → no_context (披露门)",
                             status == 200 and data.get("mode") == "no_context",
                             f"mode={data.get('mode')}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "薪酬带宽是多少？", "agent": False,
                                "external_context": True},
                               token=tokens["viewer"])
        results.append(check("8. viewer 客户在场问薪酬 → no_context (RBAC 不放宽)",
                             status == 200 and data.get("mode") == "no_context",
                             f"mode={data.get('mode')}"))

        status, data = request("POST", base, "/api/ask",
                               {"question": "薪酬带宽是多少？", "agent": False,
                                "external_context": True},
                               token=tokens["admin"])
        results.append(check("9. admin 客户在场问薪酬 → no_context (披露门收窄 admin)",
                             status == 200 and data.get("mode") == "no_context",
                             f"mode={data.get('mode')}"))

        # 10: 内部语境 GTM 简报回归
        status, data = request("POST", base, "/api/ask",
                               {"question": "计划拓展东南亚，帮我做账户情报分析并给路由建议",
                                "agent": True, "workflow": "gtm_research"},
                               token=tokens["admin"])
        results.append(check("10. 内部语境 gtm_research 账户情报 → 多跳简报不受影响",
                             status == 200 and data.get("mode") != "no_context",
                             f"子问题{len(data.get('sub_queries') or [])}个 mode={data.get('mode')}"))

        # 11-12: 审计链 (披露门问答已写入链)
        status, data = request("GET", base, "/api/audit/verify", token=tokens["admin"])
        results.append(check("11. admin 验证审计链 → 完整",
                             status == 200 and data.get("ok") is True,
                             f"{data.get('rows')} 条可验证"))

        status, _ = request("GET", base, "/api/audit/verify", token=tokens["viewer"])
        results.append(check("12. viewer 验证审计链 → 403 (治理)", status == 403, f"HTTP {status}"))

    finally:
        if server is not None:
            server.terminate()

    passed = sum(results)
    total = len(results)
    print(f"\n== GTM 披露门 demo: {passed}/{total} 通过 {'✅' if passed == total else '❌'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
