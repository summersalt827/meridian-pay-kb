#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盾牌对照演示: 同一份含 PII 与提示注入的争议记录, 开/关盾牌的差别。

用法:
    python3 scripts/demo_shield.py

为什么单独一个语料目录
----------------------
`demo_docs/` 不在主知识库 `sample_docs/` 之内, 因此:
  * 不参与相关性门的 27 组标定样本, 不会影响 test_relevance 的分可性断言
  * 不会被 web_ui 默认摄入, 线上 demo 与评测套件都不受影响

它只做一件事: 把「盾牌到底做了什么」变成肉眼可见的对照输出,
供 README 第 10 步 / 演示视频录制 / 评委自行复跑。

输出三段:
  1. 关盾牌 (模拟没有这道闸): 模型会看到什么 —— 明文卡号、NRIC、注入指令
  2. 开盾牌 (线上真实行为):   模型会看到什么 —— 标签、[instruction removed]
  3. 答案复检: 模型即便凭空编出一个卡号, 也会被拦下
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kb_qa                                    # noqa: E402
from shield import ShieldStats, rescan_answer, sanitize_for_llm  # noqa: E402

QUESTION = "争议处理结论"
BAR = "=" * 78


def main() -> int:
    cfg = kb_qa.Config(kb_root=str(ROOT / "demo_docs"), db_path=":memory:")
    kb = kb_qa.KnowledgeBase(cfg)
    kb.build()
    admin = kb_qa.User(username="alice", role="admin")

    hits = kb.retriever.search(QUESTION, admin, 4)
    if not hits:
        print("未检索到演示语料, 请确认 demo_docs/risk/dispute-log-118.md 存在")
        return 1

    print(BAR)
    print("演示 1 · 关掉盾牌: 模型会看到什么(这正是没有这道闸时的真实风险)")
    print(BAR)
    # 直接拼原文, 不走 build_context —— 等价于「未部署盾牌」的基线
    raw = "\n\n".join(
        f"[来源{i+1}] {c.path} :: {c.title}\n{c.content.strip()}"
        for i, (c, _) in enumerate(hits)
    )
    print(raw[:1200])
    print()

    print(BAR)
    print("演示 2 · 开启盾牌: 线上真实行为(need-to-know 脱敏 + 注入剔除)")
    print(BAR)
    ctx = kb_qa.Generator.build_context(hits)
    print(ctx[:1200])
    print()

    stats = ShieldStats()
    for c, _ in hits:
        sanitize_for_llm(c.content, stats)
    print(f"盾牌统计: {stats.as_dict()}")
    print()

    print(BAR)
    print("演示 3 · 答案复检: 模型凭空编出的卡号也会被拦下")
    print(BAR)
    hallucinated = "已为您查询到客户卡号 4111 1111 1111 1111，请核对。"
    st = ShieldStats()
    safe = rescan_answer(hallucinated, set(), set(), st)
    print("  模型原始输出:", hallucinated)
    print("  复检后交给用户:", safe)
    print(f"  复检统计: {st.as_dict()}")
    print()

    # ---- 断言: 演示必须真的成立(防止改代码后演示悄悄失效) ----
    checks = [
        ("关盾牌时上下文确实含明文卡号", "4111 1111 1111 1111" in raw),
        ("开盾牌后上下文不含明文卡号", "4111 1111 1111 1111" not in ctx),
        ("开盾牌后上下文不含 NRIC", "S1234567D" not in ctx),
        ("开盾牌后上下文不含 IBAN", "GB82WEST12345698765432" not in ctx),
        ("开盾牌后上下文不含注入原文", "attacker@evil.com" not in ctx),
        ("注入行留下可审计占位", "[instruction removed]" in ctx),
        ("正文业务结论被保留", "已全额退款" in ctx),
        ("凭空编造的卡号被复检拦下", "4111 1111 1111 1111" not in safe),
    ]
    print(BAR)
    print("自检")
    print(BAR)
    ok = True
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    print()
    print("对照演示自检" + ("全部通过 ✅" if ok else "存在失败 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
