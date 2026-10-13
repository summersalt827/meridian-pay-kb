#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 层验收矩阵 (问题 × 角色) + agent 工作流用例
================================================
第一部分: 18 组基线矩阵 (6 个 chips 问题 × 3 角色, agent=false 单跳基线, 行为与
  改造前一致), 断言:
  - 命中场景: mode != no_context 且 top1 路径正确
  - 拦截场景: mode == no_context 且 sources 为空
另验 /api/audit 权限 (无令牌 401, sales/viewer 403, admin 200) 与 /api/health 七角色。

第二部分: agent 工作流新用例 (10/12 会话④):
  - A1 admin 多跳: 子问题拆解生效 (agent=true, 子问题 > 1)
  - A2 sales 混合权限: 薪酬子问题被 RBAC 拦 (无 internal 泄漏), 折扣子问题命中 gtm
  - A3 gtm_research 账户情报: 四维拆解 + 简报含「需人工决策」清单
  - A4 简单问题: agent=true 时仍单跳 (agent=false, 子问题 1 个)
  - A5/A6 非法 workflow: agent=true 时 400; agent=false 时忽略不校验

鉴权 (11/28): 角色不再由请求体声明, 所有用例先 POST /api/login 换取令牌,
再以 Authorization: Bearer <token> 调用。服务需为 token 模式 (默认)。

运行前提: python3 web_ui.py 已在 localhost:8000 运行。
运行: python3 test_web_matrix.py  (退出码 0=通过, 1=有失败)
"""

from __future__ import annotations

import glob
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("KB_TEST_BASE", "http://localhost:8000")
DEMO_PASSWORD = os.getenv("KB_DEMO_PASSWORD", "meridian-demo")

# 角色 -> 演示账号 (与 auth.DEMO_ACCOUNTS 一致)
ROLE_ACCOUNT = {"admin": "alice", "sales": "sam", "viewer": "bob"}

QUESTIONS = [
    ("Q1 报销流程", "报销流程是什么？", "public/faq.md"),
    ("Q2 产品线", "我们的产品线有哪些？", "public/getting-started.md"),
    ("Q3 薪酬带宽", "薪酬带宽是多少？", "internal/roadmap.md"),
    ("Q4 结算流程", "资金账户结算流程是什么？", "internal/ops.md"),
    ("Q5 折扣审批", "大客户报价折扣能批到几折？", "gtm/pricing.md"),
    ("Q6 分层路由", "企业客户的分层路由规则是什么？", "gtm/icp-targeting.md"),
]

# role -> {问题: 期望}  hit=命中且 top1 为该文档路径; blocked=no_context
EXPECT = {
    "admin":  {"Q1 报销流程": "hit", "Q2 产品线": "hit", "Q3 薪酬带宽": "hit",
               "Q4 结算流程": "hit", "Q5 折扣审批": "hit", "Q6 分层路由": "hit"},
    "sales":  {"Q1 报销流程": "hit", "Q2 产品线": "hit", "Q3 薪酬带宽": "blocked",
               "Q4 结算流程": "blocked", "Q5 折扣审批": "hit", "Q6 分层路由": "hit"},
    "viewer": {"Q1 报销流程": "hit", "Q2 产品线": "hit", "Q3 薪酬带宽": "blocked",
               "Q4 结算流程": "blocked", "Q5 折扣审批": "blocked", "Q6 分层路由": "blocked"},
}

GTM_QUESTION = ("一家 400 人、月均 300 笔跨境付款的外贸公司刚完成 B 轮融资、"
                "计划拓展东南亚，帮我做账户情报分析并给路由建议")

# A1 原用例「新客户从签约到上线要多久？」语料并未覆盖 (只有案例里的「上线 3 个月后」
# 等零星表述), 旧实现靠撞「上线/周期」二字凑出 4 条来源——那正是相关性门要修的行为。
# 改用语料确实有答案、且需要跨两篇文档才能答好的多跳问题。
A1_QUESTION = "薪酬带宽是多少，客户分层路由规则是什么？"


def _request(method: str, path: str, body: dict | None = None,
             token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def login(role: str) -> str:
    """按角色登录, 返回令牌; 失败则抛异常 (后续用例无从谈起)。"""
    return login_raw(ROLE_ACCOUNT[role])


def login_raw(username: str) -> str:
    status, data = _request("POST", "/api/login",
                            {"username": username, "password": DEMO_PASSWORD})
    if status != 200 or not data.get("token"):
        raise SystemExit(f"登录失败 ({username}): HTTP {status} {data.get('error', '')}")
    return data["token"]
    return data["token"]


def ask(question: str, role: str, token: str, agent: bool = False,
        workflow: str = "knowledge_qa") -> tuple[int, dict]:
    body = {"question": question, "agent": agent}
    if workflow:
        body["workflow"] = workflow
    return _request("POST", "/api/ask", body, token=token)


def get_audit(token: str | None, role: str | None = None) -> int:
    """审计接口; role 仅用于构造旧式 ?role= 查询串, 验证该入口已失效。"""
    url = "/api/audit?limit=5"
    if role:
        url += "&role=" + role
    return _request("GET", url, token=token)[0]


def main() -> int:
    failures: list[str] = []
    passed = 0
    total = 0

    # ---- 登录 ----
    tokens: dict[str, str] = {}
    try:
        for role in ("admin", "sales", "viewer"):
            tokens[role] = login(role)
    except SystemExit as e:
        print(f"❌ {e}")
        print("   提示: 服务需运行在 " + BASE + " 且为 token 模式 (默认)")
        return 1
    print(f"  ✅ 登录: " + ", ".join(f"{r}={ROLE_ACCOUNT[r]}" for r in tokens) + "\n")

    # ============ 第一部分: 18 组基线矩阵 (agent=false) ============
    for role, cases in EXPECT.items():
        for name, question, doc_path in QUESTIONS:
            total += 1
            status, data = ask(question, role, tokens[role], agent=False)
            expect = cases[name]
            top1 = (data.get("sources") or [{}])[0].get("path", "") if data.get("sources") else ""
            mode = data.get("mode")

            if expect == "hit":
                ok = status == 200 and mode != "no_context" and top1 == doc_path
                detail = f"mode={mode} top1={top1 or '(无)'}"
            else:
                ok = status == 200 and mode == "no_context" and not data.get("sources")
                detail = f"mode={mode} sources={data.get('sources')}"

            if ok:
                passed += 1
                print(f"  ✅ {role:7s} × {name}: {detail}")
            else:
                failures.append(f"{role} × {name} 期望{expect}: {detail}")
                print(f"  ❌ {role:7s} × {name} 期望{expect}: {detail}")

    # 审计权限 (身份一律来自令牌)
    for label, token, want in [("无令牌", None, 401), ("sales", tokens["sales"], 403),
                               ("viewer", tokens["viewer"], 403), ("admin", tokens["admin"], 200)]:
        total += 1
        got = get_audit(token)
        ok = got == want
        if ok:
            passed += 1
            print(f"  ✅ 审计 {label}: HTTP {got}")
        else:
            failures.append(f"审计 {label}: HTTP {got} 期望 {want}")
            print(f"  ❌ 审计 {label}: HTTP {got} 期望 {want}")

    # 越权入口回归: ?role=admin 不带令牌必须仍是 401
    total += 1
    got = get_audit(None, role="admin")
    if got == 401:
        passed += 1
        print(f"  ✅ 审计 ?role=admin 无令牌: HTTP {got} (旧越权入口已失效)")
    else:
        failures.append(f"审计 ?role=admin 无令牌: HTTP {got} 期望 401")
        print(f"  ❌ 审计 ?role=admin 无令牌: HTTP {got} 期望 401")

    # ============ 第二部分: agent 工作流用例 ============
    # A1: admin 多跳拆解
    total += 1
    status, data = ask(A1_QUESTION, "admin", tokens["admin"], agent=True)
    subs = data.get("sub_queries") or []
    ok = (status == 200 and data.get("agent") is True and len(subs) > 1
          and data.get("mode") != "no_context")
    if ok:
        passed += 1
        print(f"  ✅ A1 admin 多跳: agent={data.get('agent')} 子问题{len(subs)}个 mode={data.get('mode')}")
    else:
        failures.append(f"A1 admin 多跳: agent={data.get('agent')} 子问题={subs} mode={data.get('mode')}")
        print(f"  ❌ A1 admin 多跳: agent={data.get('agent')} 子问题={subs} mode={data.get('mode')}")

    # A2: sales 混合权限 (薪酬被拦 + 折扣命中, 子层级 RBAC)
    total += 1
    status, data = ask("薪酬带宽是多少，打折规则是什么？", "sales", tokens["sales"], agent=True)
    src_paths = [s["path"] for s in data.get("sources") or []]
    leak = [p for p in src_paths if p.startswith("internal")]
    hit_pricing = any(p == "gtm/pricing.md" for p in src_paths)
    ok = (status == 200 and not leak and hit_pricing
          and data.get("mode") != "no_context")
    if ok:
        passed += 1
        print(f"  ✅ A2 sales 子层级RBAC: 泄漏={leak or '无'} 折扣命中={hit_pricing}")
    else:
        failures.append(f"A2 sales 子层级RBAC: 泄漏={leak} 折扣命中={hit_pricing}")
        print(f"  ❌ A2 sales 子层级RBAC: 泄漏={leak} 折扣命中={hit_pricing}")

    # A3: gtm_research 账户情报简报
    total += 1
    status, data = ask(GTM_QUESTION, "admin", tokens["admin"], agent=True, workflow="gtm_research")
    ans = data.get("answer") or ""
    subs = data.get("sub_queries") or []
    ok = (status == 200 and data.get("agent") is True and len(subs) > 1
          and data.get("workflow") == "gtm_research"
          and "需人工决策" in ans and "ICP" in ans
          and data.get("mode") != "no_context")
    if ok:
        passed += 1
        print(f"  ✅ A3 gtm_research 简报: 子问题{len(subs)}个 决策清单={'需人工决策' in ans}")
    else:
        failures.append(f"A3 gtm_research: agent={data.get('agent')} subs={len(subs)} "
                        f"决策清单={'需人工决策' in ans} mode={data.get('mode')}")
        print(f"  ❌ A3 gtm_research: agent={data.get('agent')} subs={len(subs)} "
              f"决策清单={'需人工决策' in ans} mode={data.get('mode')}")

    # A4: 简单问题单跳 (agent=true 也不拆)
    total += 1
    status, data = ask("报销流程是什么？", "admin", tokens["admin"], agent=True)
    subs = data.get("sub_queries") or []
    ok = (status == 200 and data.get("agent") is False and len(subs) == 1
          and data.get("mode") != "no_context")
    if ok:
        passed += 1
        print(f"  ✅ A4 简单问题单跳: agent={data.get('agent')} 子问题={subs}")
    else:
        failures.append(f"A4 简单问题单跳: agent={data.get('agent')} 子问题={subs}")
        print(f"  ❌ A4 简单问题单跳: agent={data.get('agent')} 子问题={subs}")

    # A5: 非法 workflow (agent=true) -> 400
    total += 1
    status, _ = ask("测试问题", "admin", tokens["admin"], agent=True, workflow="bad_wf")
    ok = status == 400
    if ok:
        passed += 1
        print(f"  ✅ A5 非法 workflow: HTTP {status}")
    else:
        failures.append(f"A5 非法 workflow: HTTP {status} 期望 400")
        print(f"  ❌ A5 非法 workflow: HTTP {status} 期望 400")

    # A6: agent=false 时非法 workflow 忽略
    total += 1
    status, data = ask("报销流程是什么？", "admin", tokens["admin"], agent=False, workflow="bad_wf")
    ok = status == 200
    if ok:
        passed += 1
        print(f"  ✅ A6 agent=false 忽略 workflow: HTTP {status}")
    else:
        failures.append(f"A6 agent=false 忽略 workflow: HTTP {status} 期望 200")
        print(f"  ❌ A6 agent=false 忽略 workflow: HTTP {status} 期望 200")

    # ============ 第三部分: 金融岗位新角色 (10/13 新增, 8 用例) ============
    fin_accounts = {"compliance_officer": "cara", "risk_analyst": "ray",
                    "card_ops": "erin", "finance_analyst": "fay"}
    fin_tokens: dict[str, str] = {}
    for role, username in fin_accounts.items():
        fin_tokens[role] = login_raw(username)
    fin_cases = [
        ("compliance_officer", "KYC 需要哪些文件？", "compliance/kyc-aml-policy.md"),
        ("compliance_officer", "可疑交易上报的时限是多少？", "compliance/kyc-aml-policy.md"),
        ("compliance_officer", "PDPA 数据保护的要点有哪些？", "compliance/regulatory-checklist.md"),
        ("card_ops", "企业卡发卡条件是什么？", "card_ops/card-program-rules.md"),
        ("card_ops", "可疑交易上报的时限是多少？", "blocked"),
        ("finance_analyst", "差旅住宿标准是什么？", "finance/spend-policy.md"),
        ("risk_analyst", "交易监控的异常阈值是什么？", "risk/transaction-monitoring.md"),
        ("admin", "KYC 需要哪些文件？", "compliance/kyc-aml-policy.md"),
    ]
    for role, question, expect in fin_cases:
        total += 1
        tok = tokens.get(role) or fin_tokens[role]
        status, data = ask(question, role, tok, agent=False)
        top1 = (data.get("sources") or [{}])[0].get("path", "") if data.get("sources") else ""
        mode = data.get("mode")
        if expect == "blocked":
            ok = status == 200 and mode == "no_context" and not data.get("sources")
            detail = f"mode={mode}"
        else:
            ok = status == 200 and mode != "no_context" and top1 == expect
            detail = f"mode={mode} top1={top1 or '(无)'}"
        if ok:
            passed += 1
            print(f"  ✅ F{role} × {question[:16]}: {detail}")
        else:
            failures.append(f"{role} × {question[:16]} 期望{expect}: {detail}")
            print(f"  ❌ {role} × {question[:16]} 期望{expect}: {detail}")

    # ============ 第四部分: 数据新鲜度 (resync, 10/14 新增, 5 用例) ============
    # 官方 FinTech 赛道要求: 新内容在分钟~1 小时内可检索, 绝不静默提供过期内容。
    # worked example: 写探针文档 -> resync -> 新内容可答 (引用带更新时间);
    #                  删探针文档 -> resync -> 旧内容不再可答。
    _, health = _request("GET", "/api/health")
    kb_root = health.get("kb_root") or "sample_docs"
    probe_rel = f"internal/_resync_probe_{os.getpid()}.md"
    probe_abs = os.path.join(kb_root, probe_rel)
    for leftover in glob.glob(os.path.join(kb_root, "internal", "_resync_probe_*.md")):
        os.unlink(leftover)          # 清理上次异常中断的残留

    # R1: 无令牌 resync -> 401
    total += 1
    got, _ = _request("POST", "/api/resync", {})
    ok = got == 401
    if ok:
        passed += 1
        print(f"  ✅ R1 resync 无令牌: HTTP {got}")
    else:
        failures.append(f"R1 resync 无令牌: HTTP {got} 期望 401")
        print(f"  ❌ R1 resync 无令牌: HTTP {got} 期望 401")

    # R2: viewer resync -> 403
    total += 1
    got, _ = _request("POST", "/api/resync", {}, token=tokens["viewer"])
    ok = got == 403
    if ok:
        passed += 1
        print(f"  ✅ R2 resync viewer: HTTP {got}")
    else:
        failures.append(f"R2 resync viewer: HTTP {got} 期望 403")
        print(f"  ❌ R2 resync viewer: HTTP {got} 期望 403")

    # R3: admin resync -> 200 且索引状态回填
    total += 1
    got, data = _request("POST", "/api/resync", {}, token=tokens["admin"])
    ok = (got == 200 and data.get("status") == "ok"
          and int(data.get("chunks", 0)) > 0 and int(data.get("documents", 0)) > 0
          and data.get("last_ingest_ts"))
    if ok:
        passed += 1
        print(f"  ✅ R3 resync admin: HTTP {got} chunks={data.get('chunks')} "
              f"docs={data.get('documents')} ts={data.get('last_ingest_ts')}")
    else:
        failures.append(f"R3 resync admin: HTTP {got} {data}")
        print(f"  ❌ R3 resync admin: HTTP {got} {data}")

    try:
        # R4: 新文档 -> resync -> 可答且引用带更新时间
        with open(probe_abs, "w", encoding="utf-8") as f:
            f.write("# 重扫探针\n\n重扫探针是验证数据新鲜度机制的测试文档。\n")
        _request("POST", "/api/resync", {}, token=tokens["admin"])
        total += 1
        status, data = ask("重扫探针是什么？", "admin", tokens["admin"], agent=False)
        top1 = (data.get("sources") or [{}])[0].get("path", "") if data.get("sources") else ""
        mtime = (data.get("sources") or [{}])[0].get("mtime")
        ok = status == 200 and data.get("mode") != "no_context" and top1 == probe_rel and bool(mtime)
        if ok:
            passed += 1
            print(f"  ✅ R4 新文档重扫后可答: mode={data.get('mode')} top1={top1} mtime={mtime}")
        else:
            failures.append(f"R4 新文档重扫后可答: mode={data.get('mode')} top1={top1} mtime={mtime}")
            print(f"  ❌ R4 新文档重扫后可答: mode={data.get('mode')} top1={top1} mtime={mtime}")

        # R5: 删文档 -> resync -> 旧内容不再可答
        os.unlink(probe_abs)
        _request("POST", "/api/resync", {}, token=tokens["admin"])
        total += 1
        status, data = ask("重扫探针是什么？", "admin", tokens["admin"], agent=False)
        ok = status == 200 and data.get("mode") == "no_context" and not data.get("sources")
        if ok:
            passed += 1
            print(f"  ✅ R5 删除后重扫不可答: mode={data.get('mode')}")
        else:
            failures.append(f"R5 删除后重扫不可答: mode={data.get('mode')} sources={data.get('sources')}")
            print(f"  ❌ R5 删除后重扫不可答: mode={data.get('mode')} sources={data.get('sources')}")
    finally:
        if os.path.exists(probe_abs):
            os.unlink(probe_abs)
        _request("POST", "/api/resync", {}, token=tokens["admin"])   # 恢复索引到原状态

    print(f"\n== 结果: {passed}/{total} 通过")
    if failures:
        print("失败明细:")
        for f in failures:
            print("  ❌ " + f)
        return 1
    print("== 全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
