#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
披露门 (audience gate) 测试: 「客户在场」语境下的第二道权限门
================================================================
两条不变量 (与 RBAC 金标准同构):
  1. 对外语境零泄漏 —— external_context=True 时, 来源里不允许出现 audience=internal 的分块
  2. 可对外内容零漏检 —— 该命中的 external 文档必须仍在 (防止退化成「什么都不返回」)
外加:
  - 披露门只收窄、不放宽: 读权限 (RBAC) 在对外语境下不变
  - 多跳场景下逐子问题生效 (子问题级披露)

运行: python3 test_disclosure.py  (退出码 0=通过)
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from kb_qa import Config, KnowledgeBase, User  # noqa: E402

# (名称, 用户名, 角色, 目录, external, agent, 问题, 期望mode, 应命中, 应拦截)
CASES = [
    ("回归: sales 内部语境问折扣 → 命中 pricing", "sam", "sales", ("public", "gtm"), False, False,
     "大客户报价折扣能批到几折？", None,
     ["gtm/pricing.md"], []),
    ("零泄漏: sales 客户在场问折扣 → 拦截", "sam", "sales", ("public", "gtm"), True, False,
     "大客户报价折扣能批到几折？", "no_context",
     [], ["gtm/pricing.md", "gtm/objection-playbook.md", "gtm/sales-playbook.md",
          "gtm/signal-library.md", "gtm/icp-targeting.md"]),
    ("零漏检: sales 客户在场问案例 → 命中 case-studies", "sam", "sales", ("public", "gtm"), True, False,
     "我们有哪些客户案例可以参考？", None,
     ["gtm/case-studies.md"], ["gtm/pricing.md"]),
    ("收窄 admin: admin 客户在场问薪酬 → 拦截", "alice", "admin", (), True, False,
     "薪酬带宽是多少？", "no_context",
     [], ["internal/roadmap.md", "internal/ops.md"]),
    ("external 命中: viewer 客户在场问报销 → 命中 faq", "bob", "viewer", ("public",), True, False,
     "报销流程是什么？", None,
     ["public/faq.md"], []),
    ("不放宽 RBAC: viewer 客户在场问薪酬 → 仍拦", "bob", "viewer", ("public",), True, False,
     "薪酬带宽是多少？", "no_context",
     [], ["internal/roadmap.md", "internal/ops.md", "gtm/pricing.md"]),
    ("不放宽 RBAC: sales 客户在场问合规 → 仍拦", "sam", "sales", ("public", "gtm"), True, False,
     "可疑交易上报时限是多久？", "no_context",
     [], ["compliance/kyc-aml-policy.md", "compliance/regulatory-checklist.md"]),
    ("external 命中: sales 客户在场问产品线 → 命中 getting-started", "sam", "sales", ("public", "gtm"), True, False,
     "我们的产品线有哪些？", None,
     ["public/getting-started.md"], []),
    ("多跳内部: 案例+折扣 → 案例与定价都在", "sam", "sales", ("public", "gtm"), False, True,
     "客户问我们有没有同类案例，以及能给多少折扣", None,
     ["gtm/case-studies.md", "gtm/pricing.md"], []),
    ("多跳对外: 案例+折扣 → 只剩 case-studies (子问题级)", "sam", "sales", ("public", "gtm"), True, True,
     "客户问我们有没有同类案例，以及能给多少折扣", None,
     ["gtm/case-studies.md"], ["gtm/pricing.md", "gtm/objection-playbook.md", "gtm/sales-playbook.md"]),
    ("多跳对外不变量: 所有来源 audience=external", "sam", "sales", ("public", "gtm"), True, True,
     "客户问我们有没有同类案例，以及能给多少折扣", None,
     [], []),
]


def main() -> int:
    cfg = Config(
        kb_root=os.path.join(BASE_DIR, "sample_docs"),
        # 独立测试库, 不污染演示用审计数据
        db_path=os.path.join(BASE_DIR, "kb_audit_test.db"),
        # 评测用抽取式, 不发 LLM 请求: 泄露判定只看来源列表
        llm_base_url="",
        llm_api_key="",
    )
    kb = KnowledgeBase(cfg)
    kb.build()

    passed = 0
    failures: list[str] = []

    for name, uname, role, dirs, external, agent, question, expect_mode, must_see, must_not in CASES:
        user = User(username=uname, role=role, allowed_dirs=dirs,
                    external_context=external)
        ans = kb.ask_agent(question, user) if agent else kb.ask(question, user)
        src_paths = [s["path"] for s in ans.sources]
        problems: list[str] = []

        if expect_mode and ans.mode != expect_mode:
            problems.append(f"mode={ans.mode} 期望 {expect_mode}")
        for p in must_see:
            if not any(p == s or s.startswith(p) for s in src_paths):
                problems.append(f"漏检 {p} (sources={src_paths[:5]})")
        for p in must_not:
            if any(s.startswith(p) for s in src_paths):
                problems.append(f"泄漏 {p} (sources={src_paths[:5]})")
        # 对外语境强不变量: 任何来源不得是 internal audience
        if external and not agent and not problems:
            leaks = [s["path"] for s in ans.sources if s.get("audience") != "external"]
            if leaks:
                problems.append(f"audience 不变量被破坏: {leaks}")
        # 用例 11: 多跳对外语境的不变量
        if external and agent:
            leaks = [s["path"] for s in ans.sources if s.get("audience") != "external"]
            if leaks:
                problems.append(f"多跳 audience 不变量被破坏: {leaks}")

        if problems:
            failures.append(f"{name}: " + "; ".join(problems))
            print(f"  ❌ {name}: " + "; ".join(problems))
        else:
            passed += 1
            print(f"  ✅ {name}: mode={ans.mode} sources={[p for p in src_paths][:4]}")

    print(f"\n== 披露门: {passed}/{passed + len(failures)} 通过")
    if failures:
        print("失败用例:")
        for f in failures:
            print("  - " + f)
        return 1
    print("== 全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
