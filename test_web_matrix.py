#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web 层验收矩阵 (问题 × 角色) + agent 工作流用例
================================================
第一部分: 18 组基线矩阵 (6 个 chips 问题 × 3 角色, agent=false 单跳基线, 行为与
  改造前一致), 断言:
  - 命中场景: mode != no_context 且 top1 路径正确
  - 拦截场景: mode == no_context 且 sources 为空
另验 /api/audit 权限 (sales/viewer/缺角色 403, admin 200) 与 /api/health 三角色。

第二部分: agent 工作流新用例 (10/12 会话④):
  - A1 admin 多跳: 子问题拆解生效 (agent=true, 子问题 > 1)
  - A2 sales 混合权限: 薪酬子问题被 RBAC 拦 (无 internal 泄漏), 折扣子问题命中 gtm
  - A3 gtm_research 账户情报: 四维拆解 + 简报含「需人工决策」清单
  - A4 简单问题: agent=true 时仍单跳 (agent=false, 子问题 1 个)
  - A5/A6 非法 workflow: agent=true 时 400; agent=false 时忽略不校验

运行前提: python3 web_ui.py 已在 localhost:8000 运行 (LLM 模式)。
运行: python3 test_web_matrix.py  (退出码 0=通过, 1=有失败)
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://localhost:8000"

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


def ask(question: str, role: str, username: str = "tester",
        agent: bool = False, workflow: str = "knowledge_qa") -> tuple[int, dict]:
    body = {"question": question, "username": username, "role": role, "agent": agent}
    if workflow:
        body["workflow"] = workflow
    req = urllib.request.Request(
        BASE + "/api/ask",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get_audit(role: str | None) -> int:
    url = BASE + "/api/audit?limit=5"
    if role:
        url += "&role=" + role
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    failures: list[str] = []
    passed = 0
    total = 0

    # ============ 第一部分: 18 组基线矩阵 (agent=false) ============
    for role, cases in EXPECT.items():
        for name, question, doc_path in QUESTIONS:
            total += 1
            status, data = ask(question, role, agent=False)
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

    # 审计权限
    for label, role, want in [("sales", "sales", 403), ("viewer", "viewer", 403),
                              ("缺角色", None, 403), ("admin", "admin", 200)]:
        total += 1
        got = get_audit(role)
        ok = got == want
        if ok:
            passed += 1
            print(f"  ✅ 审计 {label}: HTTP {got}")
        else:
            failures.append(f"审计 {label}: HTTP {got} 期望 {want}")
            print(f"  ❌ 审计 {label}: HTTP {got} 期望 {want}")

    # ============ 第二部分: agent 工作流用例 ============
    # A1: admin 多跳拆解
    total += 1
    status, data = ask("新客户从签约到上线要多久？", "admin", agent=True)
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
    status, data = ask("薪酬带宽是多少，打折规则是什么？", "sales", agent=True)
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
    status, data = ask(GTM_QUESTION, "admin", agent=True, workflow="gtm_research")
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
    status, data = ask("报销流程是什么？", "admin", agent=True)
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
    status, _ = ask("测试问题", "admin", agent=True, workflow="bad_wf")
    ok = status == 400
    if ok:
        passed += 1
        print(f"  ✅ A5 非法 workflow: HTTP {status}")
    else:
        failures.append(f"A5 非法 workflow: HTTP {status} 期望 400")
        print(f"  ❌ A5 非法 workflow: HTTP {status} 期望 400")

    # A6: agent=false 时非法 workflow 忽略
    total += 1
    status, data = ask("报销流程是什么？", "admin", agent=False, workflow="bad_wf")
    ok = status == 200
    if ok:
        passed += 1
        print(f"  ✅ A6 agent=false 忽略 workflow: HTTP {status}")
    else:
        failures.append(f"A6 agent=false 忽略 workflow: HTTP {status} 期望 200")
        print(f"  ❌ A6 agent=false 忽略 workflow: HTTP {status} 期望 200")

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
