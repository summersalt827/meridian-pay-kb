#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 10 套: GTM 首触邮件草稿 (outreach.py) —— 确定性填槽与「不编造」边界。

为什么单独一套
-------------
synth_prompt 向用户承诺的六节简报里, 第五节「首触邮件草稿」改造前只有 LLM 能写:
没配 key 时离线简报的这一节退化成两条按关键词捞出来的异议话术片段 —— 章节在,
内容不在。outreach.py 把这一节变成按 tier + 行业 + 最强信号填槽的确定性模板。

本套测四件事:

1. **可复现** —— 同一张评分卡永远得到同一封草稿 (与评分卡同源的纪律)。
2. **结构完整** —— 主题行 ≤8 个英文词、正文 3-4 句、合规注记四要素齐全。
   这是 synth_prompt 对模型的硬要求, 模板必须自己先做到。
3. **槽位真的在填** —— tier 换 CTA 换, 行业换价值主张与案例换,
   最强信号换开场白换。只测「不崩」的话, 一个对所有输入输出同一封邮件的
   实现也能跑通 —— 那是把模板伪装成个性化。
4. **不越界** —— 命中红线时不产出任何对外文案; 事实不足时给待补清单而不是
   一封编出来的邮件; 正文里的每个数字都能指回模板常量或用户提供的事实。

第四组是刻意加的: 对外文案的失败模式和评分卡不一样, 评分卡错了是「低估客户」,
邮件错了是「替公司向客户做出不存在的承诺」。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from outreach import (  # noqa: E402
    BANNED_PHRASES, COMPLIANCE_NOTE, DEFAULT_PROFILE, INDUSTRY_PROFILES,
    draft_outreach, scan_banned, template_numbers,
)
from scorecard import score_account  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def draft_for(q: str, proof_ref: str = "来源1"):
    return draft_outreach(score_account(q), proof_ref)


# ============================================================
# A. 可复现性 —— 确定性填槽的底线
# ============================================================
print("A 组 · 可复现性（同一评分卡必须永远同一封草稿）")

Q_HOT = "调研外贸出口公司，B 轮融资，300 人，正在扩编财务团队，CFO 内推"
drafts = [draft_outreach(score_account(Q_HOT), "来源1") for _ in range(5)]
check("A1 五次生成主题行一致", len({d.subject for d in drafts}) == 1,
      f"得到 {[d.subject for d in drafts]}")
check("A2 五次生成正文一致",
      all(d.body == drafts[0].body for d in drafts))
check("A3 五次渲染文本完全一致", len({d.as_markdown() for d in drafts}) == 1)
check("A4 五次 sendable 状态一致", len({d.sendable for d in drafts}) == 1)

# ============================================================
# B. 结构完整性 —— synth_prompt 对模型的硬要求, 模板自己先做到
# ============================================================
print("\nB 组 · 结构完整（主题行 / 正文 / 合规注记）")

d_hot = drafts[0]
check("B1 主题行 ≤8 个英文词", 0 < d_hot.subject_words() <= 8,
      f"{d_hot.subject!r} = {d_hot.subject_words()} 词")
check("B2 正文 3-4 句", 3 <= len(d_hot.body) <= 4, f"{len(d_hot.body)} 句")
check("B3 每句非空且以句号/问号收尾",
      all(s.strip() and s.rstrip()[-1] in "。？" for s in d_hot.body))
check("B4 合规注记含 PDPA", "PDPA" in COMPLIANCE_NOTE and "PDPA" in d_hot.compliance)
check("B5 合规注记含 CAN-SPAM 退订机制", "CAN-SPAM" in d_hot.compliance)
check("B6 合规注记声明为草稿且需经理批准",
      "草稿" in d_hot.compliance and "销售经理批准" in d_hot.compliance)
check("B7 渲染含主题行 / 正文 / 合规注记三段",
      all(k in d_hot.as_markdown() for k in ("主题行", "正文", "合规注记")))
check("B8 渲染含钩子信号与素材锚点（可追溯）",
      "钩子信号" in d_hot.as_markdown() and "素材锚点" in d_hot.as_markdown())
check("B9 传入的来源编号进了素材锚点", "来源1" in d_hot.as_markdown())

# 每个行业画像的主题行都要过 ≤8 词 (含 Cold 变体)
for _key, _p in list(INDUSTRY_PROFILES.items()) + [("default", DEFAULT_PROFILE)]:
    for _sub in (_p.subject, _p.subject_cold):
        _n = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", _sub))
        check(f"B10 {_key} 主题行 ≤8 词", _n <= 8, f"{_sub!r} = {_n} 词")

# ============================================================
# C. 槽位真的在填 —— 不是对所有输入输出同一封邮件
# ============================================================
print("\nC 组 · 槽位填充（tier / 行业 / 最强信号各自改变产出）")

Q_WARM = "外贸公司，20 人，B 轮融资，官网自助注册，正在扩编财务团队"
Q_COLD = "外贸公司，20 人，B 轮融资，官网自助注册，下载了我们的白皮书"
d_warm, d_cold = draft_for(Q_WARM), draft_for(Q_COLD)
check("C1 Warm 与 Cold 的 tier 确实不同",
      score_account(Q_WARM).tier == "Warm" and score_account(Q_COLD).tier == "Cold",
      f"{score_account(Q_WARM).tier} / {score_account(Q_COLD).tier}")
check("C2 Hot CTA 约 20 分钟", "20 分钟" in d_hot.body[-1], d_hot.body[-1])
check("C3 Warm CTA 约 15 分钟电话", "15 分钟" in d_warm.body[-1], d_warm.body[-1])
check("C4 Cold CTA 不约会议（培育 + 季度末重评）",
      "季度末" in d_cold.body[-1] and "不占你时间" in d_cold.body[-1], d_cold.body[-1])
check("C5 Cold 用培育版主题行", d_cold.subject != d_hot.subject
      and d_cold.subject.startswith("Insight:"), d_cold.subject)

d_retail = draft_for("连锁零售企业，马来西亚，B 轮融资，120 人，CFO 内推")
check("C6 行业换 → 价值主张换", d_retail.body[1] != d_hot.body[1],
      f"{d_retail.body[1]} vs {d_hot.body[1]}")
check("C7 行业换 → 引用的案例换", d_retail.body[2] != d_hot.body[2])
check("C8 行业换 → 建议收件人换", d_retail.decision_maker != d_hot.decision_maker,
      f"{d_retail.decision_maker} / {d_hot.decision_maker}")
check("C9 行业换 → 主题行换", d_retail.subject != d_hot.subject)
_unique_profiles = {id(p) for p in INDUSTRY_PROFILES.values()}
check("C10 五个行业画像各有独立价值主张与交付物",
      len(_unique_profiles) == 5
      and len({p.deliverable for p in INDUSTRY_PROFILES.values()}) == 5
      and len({p.value for p in INDUSTRY_PROFILES.values()}) == 5,
      f"画像数={len(_unique_profiles)}")

# 最强信号维度不同 → 开场白不同
d_timing = draft_for("外贸公司，B 轮融资")
d_intent = draft_for("外贸公司，正在扩编财务团队")
d_fit = draft_for("外贸公司，300 人")
d_access = draft_for("CFO 内推")
hooks = {"Timing": d_timing, "Intent": d_intent, "Fit": d_fit, "Access": d_access}
for k, d in hooks.items():
    check(f"C11 {k} 场景钩子取自 {k} 维度", d.hook_key == k,
          f"hook_key={d.hook_key} body0={d.body[0] if d.body else ''!r}")
check("C12 四种钩子的开场白互不相同",
      len({d.body[0] for d in hooks.values()}) == 4,
      str([d.body[0][:20] for d in hooks.values()]))
check("C13 融资钩子引用具体轮次", "B 轮" in d_timing.body[0], d_timing.body[0])
check("C14 规模钩子引用具体人数", "300" in d_fit.body[0], d_fit.body[0])

# ============================================================
# D. 边界 —— 红线不产出文案, 事实不足不硬编
# ============================================================
print("\nD 组 · 边界（红线 / 事实不足 / 部分缺事实）")

d_red = draft_for("虚拟货币交易所，B 轮融资，500 人，CFO 内推")
check("D1 红线 → blocked", d_red.blocked)
check("D2 红线 → 不产出主题行", d_red.subject == "")
check("D3 红线 → 不产出正文", d_red.body == [])
check("D4 红线渲染里声明「不生成」并指向上报合规",
      "不生成" in d_red.as_markdown() and "上报合规" in d_red.as_markdown())
check("D5 红线渲染里不含任何对外文案痕迹",
      not any(w in d_red.as_markdown() for w in ("看到贵司", "主题行", "退订")),
      d_red.as_markdown())
check("D6 红线 → 不可发送", not d_red.sendable)

d_none = draft_for("调研一下这家公司")
check("D7 四维全缺事实 → 骨架而非可发送草稿", d_none.skeleton and not d_none.sendable)
check("D8 骨架给待补事实清单（四维各一条）",
      d_none.as_markdown().count("- [ ]") == 4)
check("D9 骨架正文留待填槽位", "【待填" in d_none.body[0], d_none.body[0])
check("D10 骨架仍保留合规注记（任何对外素材都要带）",
      COMPLIANCE_NOTE.split(";")[0] in d_none.as_markdown())

d_partial = draft_for("外贸公司，20 人，月跨境付款 10 笔")
check("D11 部分缺事实 → 仍出草稿（不因谨慎而消失）", d_partial.sendable)
check("D12 部分缺事实 → 声明缺事实维度并重生成提示",
      "缺事实" in d_partial.caveat and "重生成" in d_partial.caveat, d_partial.caveat)
check("D13 部分缺事实 → 声明总分为下限（与评分卡口径一致）",
      "下限" in score_account("外贸公司，20 人，月跨境付款 10 笔").as_markdown())

# ============================================================
# E. 禁用词 —— LLM 可能违规, 确定性模板不可能
# ============================================================
print("\nE 组 · 禁用词扫描（synth_prompt 的 AI 味词表）")

MATRIX = [
    Q_HOT, Q_WARM, Q_COLD, "调研一下这家公司", "虚拟货币交易所，B 轮融资",
    "连锁零售企业，38 家门店，B 轮融资，内推",
    "出海 SaaS 公司，印尼，C 轮融资，CFO 内推，正在扩编财务团队",
    "跨境电商卖家，深圳，A 轮融资，官网提交了表单",
    "物流公司，马来西亚，新开东南亚主体，区域运营总监",
    "咨询公司，新加坡，下载了我们的白皮书",
    "制造工厂，300 人，B 轮融资，财务总监内推",
]
hits: list[str] = []
for q in MATRIX:
    d = draft_for(q)
    found = scan_banned(d.as_markdown())
    if found:
        hits.append(f"{q[:12]}… -> {found}")
check("E1 输入矩阵内无任何禁用词", not hits, str(hits))
check("E2 禁用词表与 synth_prompt 一致（≥13 条）", len(BANNED_PHRASES) >= 13,
      str(len(BANNED_PHRASES)))
check("E3 扫描器本身能抓到禁用词", scan_banned("我们帮您赋能、一站式闭环") != [])

# ============================================================
# F. 不编造数字 —— 正文里的每个数字都有出处
# ============================================================
print("\nF 组 · 不编造数字（数字只能来自模板常量或用户提供的事实）")

ALLOWED = template_numbers()
bad: list[str] = []
for q in MATRIX + ["外贸公司，420 人，月跨境付款 450 笔，B 轮融资，CFO 内推"]:
    c = score_account(q)
    d = draft_outreach(c, "来源1")
    facts = {str(c.headcount), str(c.txn)}
    text = " ".join(d.body) + " " + d.subject
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        if num not in ALLOWED and num not in facts:
            bad.append(f"{q[:12]}… -> {num}")
check("F1 正文与主题行内无来源不明的数字", not bad, str(bad))
check("F2 模板数字白名单非空（白名单失效会让 F1 假绿）", len(ALLOWED) >= 8,
      str(sorted(ALLOWED)))
_d_fit_only = draft_for("外贸公司，420 人，月跨境付款 450 笔")
check("F3 用户提供的人数进开场白（事实数字可用）",
      _d_fit_only.hook_key == "Fit" and "420" in _d_fit_only.body[0],
      f"hook={_d_fit_only.hook_key} body0={_d_fit_only.body[0]!r}")
check("F4 案例数字全部锚定 case-studies",
      all(p.proof_doc.startswith("case-studies")
          for p in list(INDUSTRY_PROFILES.values()) + [DEFAULT_PROFILE]))

# ============================================================
# G. 与评分卡联动 —— 钩子必须来自最强信号维度
# ============================================================
print("\nG 组 · 与评分卡联动（钩子 = 最强非 Access 信号）")

for q in (Q_HOT, Q_WARM, Q_COLD, "外贸公司，正在扩编财务团队", "外贸公司，300 人"):
    c = score_account(q)
    d = draft_outreach(c)
    scored = [x for x in c.dims if x.score > 0 and x.evidence.strip()]
    non_access = [x for x in scored if x.key != "Access"] or scored
    top = max(x.score for x in non_access)
    check(f"G {q[:10]}… 钩子分数 = 最强信号分数({top})", d.hook_score == top,
          f"hook={d.hook_key}/{d.hook_score} 期望分数={top}")
    check(f"G {q[:10]}… 钩子维度在候选内",
          d.hook_key in {x.key for x in non_access}, d.hook_key)

c_hot = score_account(Q_HOT)
check("G9 同分时 Timing 优先于 Fit（开场白用时机而非画像）",
      draft_for(Q_HOT).hook_key == "Timing",
      f"Fit={[x.score for x in c_hot.dims if x.key == 'Fit']}"
      f" Timing={[x.score for x in c_hot.dims if x.key == 'Timing']}")
check("G10 Access 只在别无选择时做钩子（不拿触达路径开场）",
      draft_for("外贸公司，B 轮融资，CFO 内推").hook_key != "Access")

# ============================================================
# H. 输入鲁棒性 —— 空 / 超长 / 乱码不该炸
# ============================================================
print("\nH 组 · 输入鲁棒性")

for bad_in in ("", "   ", "???", "调研" * 500, "外贸" * 300):
    try:
        dd = draft_for(bad_in)
        ok = isinstance(dd.subject, str) and 0 <= dd.subject_words() <= 8
        ok = ok and (dd.blocked or dd.skeleton or 3 <= len(dd.body) <= 4)
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"     异常: {e}")
    check(f"H 异常输入 len={len(bad_in)} 不崩溃且产出合法", ok)

check("H6 空评分卡也不崩（下游误传时兜底）",
      draft_outreach(score_account("")).skeleton)

# ============================================================
# I. 集成 —— 离线简报的第五节不再只有话术片段
# ============================================================
print("\nI 组 · 集成（离线简报第五节真的填上了）")

import kb_qa  # noqa: E402
from kb_qa import Chunk, WORKFLOWS, build_offline_report  # noqa: E402

_SRC = [
    Chunk(doc_id="c1", path="gtm/case-studies.md", title="客户案例集",
          content="案例一：跨境 B2B 贸易商（新加坡，120 人），到账 T+3 压到 T+1。"),
    Chunk(doc_id="c2", path="gtm/icp-targeting.md", title="ICP 分层与线索路由规则",
          content="三层客户画像与分层路由规则。"),
    Chunk(doc_id="c3", path="gtm/objection-playbook.md", title="异议处理库",
          content="「太贵了」：先换算单笔成本与人天。"),
]
sources = [(c, 0.5) for c in _SRC]
wf = WORKFLOWS["gtm_research"]
rep = build_offline_report(Q_HOT, sources, wf, role="sales", ts="2026-01-01 00:00")
rep2 = build_offline_report(Q_HOT, sources, wf, role="sales", ts="2026-01-01 00:00")


def section(text: str, head: str) -> str:
    """取出简报里某一节的正文（到下一个二级标题为止）。"""
    parts = text.split(head, 1)
    if len(parts) < 2:
        return ""
    return parts[1].split("\n## ", 1)[0]


sec5 = section(rep, "## 五、首触邮件草稿")
check("I1 第五节存在", "## 五、首触邮件草稿" in rep)
check("I2 第五节含主题行", "主题行" in sec5)
check("I3 第五节含合规注记", "合规注记" in sec5)
check("I4 第五节不再是「资料未覆盖」", "资料未覆盖" not in sec5, sec5[:120])
check("I5 第五节引用了真实来源编号", "来源1" in sec5, sec5[:200])
check("I6 第五节素材锚点指向 case-studies", "case-studies" in sec5 or "来源1" in sec5)
check("I7 第五节附回复预案（异议素材）", "回复预案" in sec5, sec5[-160:])
check("I8 两次生成第五节完全一致（可复现）", section(rep2, "## 五、首触邮件草稿") == sec5)

rep_red = build_offline_report("虚拟货币交易所，B 轮融资，500 人，CFO 内推",
                               sources, wf, role="sales")
sec5_red = section(rep_red, "## 五、首触邮件草稿")
check("I9 红线时第五节明确不生成邮件", "不生成" in sec5_red, sec5_red[:120])
check("I10 红线时第五节不含主题行", "主题行" not in sec5_red)

rep_none = build_offline_report("调研一下这家公司", sources, wf, role="sales")
sec5_none = section(rep_none, "## 五、首触邮件草稿")
check("I11 事实不足时第五节给待补清单", "待补事实" in sec5_none, sec5_none[:120])

print("\n" + "=" * 56)
print(f"== 评测结果: {PASS}/{PASS + FAIL} 通过" + (" ✅" if FAIL == 0 else f" ❌ 失败 {FAIL}"))
sys.exit(1 if FAIL else 0)
