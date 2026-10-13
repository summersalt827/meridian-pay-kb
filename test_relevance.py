#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
相关性门评测 (修复「归一化分阈值失效」的回归测试)
==================================================
背景: 旧口径把 BM25 与 TF-IDF 各自 min-max 归一化后加权, 排第一的分数恒接近 1.0,
阈值 0.3 几乎从不触发——知识库里完全不存在的话题也能带著 4 条来源过门, admin 视角
会把 internal 的结算 SOP 原样吐出来。新口径改用绝对可比的 relevance (BM25 分 /
该查询的 BM25 自匹配上界), 阈值按正负样本实测标定为 0.17。

本文件断言三件事, 缺一不可:
  1. 语料里有答案的提问必须命中, 且 top1 落在正确文档  —— 防「门太严, 什么都不返回」
  2. 语料里没有答案的提问必须 no_context, 来源为空      —— 防「门太松, 撞字就凑数」
  3. 两类提问的原始 relevance 必须完全分离             —— 防日后改分词/打分悄悄退化

运行: python3 test_relevance.py   (退出码 0=通过, 1=有失败)
"""

from __future__ import annotations

import os
import sys

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from kb_qa import Config, KnowledgeBase, User, tokenize  # noqa: E402

# ---- 语料里有答案: 必须命中, 且 top1 为该文档 ----
# (问题, 期望 top1 路径)
MUST_HIT = [
    ("报销流程是什么？", "public/faq.md"),
    ("我们的产品线有哪些？", "public/getting-started.md"),
    ("薪酬带宽是多少？", "internal/roadmap.md"),
    ("资金账户结算流程是什么？", "internal/ops.md"),
    ("大客户报价折扣能批到几折？", "gtm/pricing.md"),
    ("企业客户的分层路由规则是什么？", "gtm/icp-targeting.md"),
    ("年假可以休多少天", "public/faq.md"),                 # 弱提问: 语料确有答案
    ("员工入职需要准备哪些材料", "public/getting-started.md"),
]

# ---- 语料里没有答案: 必须判无命中 ----
# 注: 这些提问刻意混入语料高频词 (公司/客户/处理), 专门检验「撞一个常见字就过门」
MUST_REFUSE = [
    "量子计算与区块链共识算法的关系",
    "今天天气怎么样",
    "周末去哪里钓鱼比较好",
    "公司年会在哪个城市举办",
    "食堂饭菜怎么投诉",
    "服务器宕机了怎么办",
    "老板的电话号码是多少",
    "Python 装饰器与元类的区别",
    "asdfghjkl",
]


def raw_top1(kb: KnowledgeBase, question: str) -> float:
    """绕过 min_score 门, 取该提问在全库的最高 relevance (用于可分性断言)。"""
    retriever = kb.retriever
    assert retriever is not None
    q_tokens = tokenize(question)
    ceiling = kb.bm25.self_ceiling(q_tokens)
    if ceiling <= 0:
        return 0.0
    scores = kb.bm25.score(q_tokens)
    best = max(range(len(kb.chunks)), key=lambda i: float(scores[i]))
    return retriever.relevance(q_tokens, best, ceiling=ceiling, bm25_raw=float(scores[best]))


def main() -> int:
    cfg = Config(
        kb_root=os.path.join(BASE_DIR, "sample_docs"),
        db_path=os.path.join(BASE_DIR, "kb_audit_test.db"),
        llm_base_url="",              # 抽取式: 本文件只看来源, 不发 LLM 请求
        llm_api_key="",
    )
    kb = KnowledgeBase(cfg)
    kb.build()
    admin = User(username="alice", role="admin", allowed_dirs=())

    failures: list[str] = []
    passed = 0
    total = 0

    print("== 一、必须命中 (防门太严) ==")
    hit_scores: list[float] = []
    for question, expect_doc in MUST_HIT:
        total += 1
        ans = kb.ask(question, admin)
        top1 = ans.sources[0]["path"] if ans.sources else "(无来源)"
        score = ans.sources[0]["score"] if ans.sources else 0.0
        hit_scores.append(score)
        ok = ans.mode != "no_context" and top1 == expect_doc
        if ok:
            passed += 1
            print(f"  ✅ {question[:22]:<24} rel={score:.4f}  {top1}")
        else:
            failures.append(f"应命中却未命中: {question} -> mode={ans.mode} top1={top1}")
            print(f"  ❌ {question[:22]:<24} rel={score:.4f}  {top1} (期望 {expect_doc})")

    print("\n== 二、必须判无命中 (防门太松) ==")
    refuse_scores: list[float] = []
    for question in MUST_REFUSE:
        total += 1
        ans = kb.ask(question, admin)
        refuse_scores.append(raw_top1(kb, question))
        ok = ans.mode == "no_context" and not ans.sources
        if ok:
            passed += 1
            print(f"  ✅ {question[:22]:<24} mode={ans.mode} 来源={len(ans.sources)}")
        else:
            leaked = [s["path"] for s in ans.sources]
            failures.append(f"无关提问未拦截: {question} -> mode={ans.mode} 来源={leaked}")
            print(f"  ❌ {question[:22]:<24} mode={ans.mode} 来源={leaked}")

    print("\n== 三、多跳 agent 同样过门 ==")
    for question in MUST_REFUSE[:3]:
        total += 1
        ans = kb.ask_agent(question, admin, workflow_id="knowledge_qa")
        ok = ans.mode == "no_context" and not ans.sources
        if ok:
            passed += 1
            print(f"  ✅ agent {question[:20]:<22} mode={ans.mode} 来源={len(ans.sources)}")
        else:
            failures.append(f"agent 无关提问未拦截: {question}")
            print(f"  ❌ agent {question[:20]:<22} mode={ans.mode} 来源={len(ans.sources)}")

    # ---- 可分性: 阈值两侧必须留有空隙 ----
    print("\n== 四、分数可分性 (阈值标定是否仍然成立) ==")
    total += 1
    hit_min = min(hit_scores)
    miss_max = max(refuse_scores)
    threshold = cfg.min_score
    separable = miss_max < threshold < hit_min
    if separable:
        passed += 1
        print(f"  ✅ 可分: 无关最高 {miss_max:.4f} < 阈值 {threshold} < 应命中最低 {hit_min:.4f}")
        print(f"     下侧余量 {threshold / miss_max:.2f}x   上侧余量 {hit_min / threshold:.2f}x")
    else:
        failures.append(
            f"分数区间重叠或阈值越界: 无关最高={miss_max:.4f} 阈值={threshold} 应命中最低={hit_min:.4f}"
        )
        print(f"  ❌ 不可分: 无关最高 {miss_max:.4f} / 阈值 {threshold} / 应命中最低 {hit_min:.4f}")

    print(f"\n== 结果: {passed}/{total} 通过")
    if failures:
        print("失败明细:")
        for f in failures:
            print("  ❌ " + f)
        return 1
    print("== 全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
