#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RBAC 金标准评测 (VaultRAG 思路)
================================
对 (用户, 角色, 问题) 黄金集断言两个指标:
  1. leak rate  = 0   —— 用户无权查看的文档, 一个分块都不允许出现在来源里
  2. recall     = 100% —— 用户有权查看且应命中的文档, 必须出现在来源里

两个指标必须成对看: 只测 leak 会退化成「什么都不返回」, 只测 recall 会退化成「什么都返回」。

语料 = Meridian Pay fintech 文档集 (8 篇: public×2 / internal×2 / gtm×4),
三角色: admin 不限 / sales public+gtm / viewer 仅 public。
运行: python3 test_rbac.py  (退出码 0=通过, 1=有失败或泄露)
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from kb_qa import Config, KnowledgeBase, User  # noqa: E402

# (名称, 用户名, 角色, 可访问目录, 问题, 期望mode(可None), 应命中, 应拦截)
CASES = [
    ("admin 问薪酬带宽 → 命中 internal", "alice", "admin", (),
     "薪酬带宽是多少？", None,
     ["internal/roadmap.md"], []),
    ("viewer 问薪酬带宽 → 整体拦截", "bob", "viewer", ("public",),
     "薪酬带宽是多少？", "no_context",
     [], ["internal/roadmap.md", "internal/ops.md", "gtm/pricing.md"]),
    ("sales 问薪酬带宽 → 拦截 internal", "sam", "sales", ("public", "gtm"),
     "薪酬带宽是多少？", "no_context",
     [], ["internal/roadmap.md", "internal/ops.md"]),
    ("sales 问折扣审批 → 命中 gtm", "sam", "sales", ("public", "gtm"),
     "大客户报价折扣能批到几折？", None,
     ["gtm/pricing.md"], ["internal/roadmap.md", "internal/ops.md"]),
    ("viewer 问折扣审批 → 拦截 gtm+internal", "bob", "viewer", ("public",),
     "大客户报价折扣能批到几折？", "no_context",
     [], ["gtm/pricing.md", "gtm/icp-targeting.md", "internal/roadmap.md", "internal/ops.md"]),
    ("viewer 问报销流程 → 命中 public", "bob", "viewer", ("public",),
     "报销流程是什么？", None,
     ["public/faq.md"], ["internal/roadmap.md", "internal/ops.md", "gtm/pricing.md"]),
    ("sales 问结算流程 → 拦截 internal", "sam", "sales", ("public", "gtm"),
     "资金账户结算流程是什么？", "no_context",
     [], ["internal/ops.md"]),
    ("admin 问分层路由 → 命中 gtm", "alice", "admin", (),
     "企业客户的分层路由规则是什么？", None,
     ["gtm/icp-targeting.md"], []),
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
    leaks: list[str] = []
    recall_misses: list[str] = []

    for name, uname, role, dirs, question, expect_mode, must_see, must_not_see in CASES:
        user = User(username=uname, role=role, allowed_dirs=dirs)
        ans = kb.ask(question, user)
        src_paths = [s["path"] for s in ans.sources]
        problems: list[str] = []

        if expect_mode and ans.mode != expect_mode:
            problems.append(f"mode={ans.mode} 期望 {expect_mode}")

        for p in must_see:
            if not any(p in sp for sp in src_paths):
                recall_misses.append(f"[{name}] 应命中未命中: {p}")
                problems.append(f"recall 漏检 {p}")

        for p in must_not_see:
            if any(p in sp for sp in src_paths):
                leaks.append(f"[{name}] 越权文档进入来源: {p}")
                problems.append(f"LEAK {p}")

        ok = not problems
        passed += ok
        src_short = [sp for sp in src_paths] or ["(无来源)"]
        mark = "✅" if ok else "❌"
        detail = f"  —  {'; '.join(problems)}" if problems else ""
        print(f"{mark} {name}")
        print(f"    mode={ans.mode}  sources={src_short}{detail}")

    total = len(CASES)
    leak_rate = len(leaks)
    print(f"\n== 评测结果: {passed}/{total} 通过")
    print(f"== leak rate = {leak_rate} (目标 0)   recall 漏检 = {len(recall_misses)} 条 (目标 0)")
    if leaks:
        print("泄露明细:")
        for l in leaks:
            print("  ❌ " + l)
    if recall_misses:
        print("漏检明细:")
        for m in recall_misses:
            print("  ⚠️ " + m)
    return 1 if leaks or recall_misses or passed < total else 0


if __name__ == "__main__":
    raise SystemExit(main())
