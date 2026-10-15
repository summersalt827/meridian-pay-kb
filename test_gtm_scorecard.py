#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 9 套: GTM 账户情报评分卡 (scorecard.py) —— 确定性评分的可复现性与正确性。

为什么单独一套
-------------
评分卡是 gtm_research 向用户承诺的核心产出 (四维各 0-25、总分 100、映射 tier)。
改造前这个承诺没有代码兜底: LLM 路径靠模型自由发挥, 离线路径根本没有分数。
补齐之后必须有断言把它钉死, 否则它会在下一次重构里悄悄退化回「模型说了算」。

本套测三件事, 缺一不可:

1. **可复现** —— 同一输入跑多次必须完全同分。这是「可审计」的前提:
   一个两次运行给出不同分的评分卡, 谈不上公平性 (MAS FEAT fairness)。
2. **规则正确** —— 每一分能指回 icp-targeting.md §6 的具体条款档位,
   不是拍脑袋的整数。
3. **不越界** —— 红线优先于分数; 缺事实记 0 但不等于「不追」;
   部分缺事实时必须声明分数是下限 (否则销售会低估客户)。

第三组是刻意加的: 只测「分数对不对」的话, 一个把所有不确定输入都判成
「不追」的实现也能拿满分 —— 那是把谨慎伪装成安全。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorecard import score_account, Dimension  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ============================================================
# A. 可复现性 —— 确定性评分的底线
# ============================================================
print("A 组 · 可复现性（同一输入必须永远同一输出）")

REPRO_Q = "调研东南亚跨境支付公司 Nium，B 轮融资，300 人，扩编财务团队，CFO 内推"
cards = [score_account(REPRO_Q) for _ in range(5)]
check("A1 五次运行总分一致", len({c.total for c in cards}) == 1,
      f"得到 {[c.total for c in cards]}")
check("A2 五次运行 tier 一致", len({c.tier for c in cards}) == 1,
      f"得到 {[c.tier for c in cards]}")
check("A3 五次运行四维逐项一致",
      all([(d.key, d.score) for d in c.dims] == [(d.key, d.score) for d in cards[0].dims]
          for c in cards))
check("A4 渲染文本完全一致", len({c.as_markdown() for c in cards}) == 1)

# ============================================================
# B. 规则正确性 —— 每一分指回 §6 条款
# ============================================================
print("\nB 组 · 规则正确性（映射到 icp-targeting.md §6 档位）")

# B1: 满配客户 —— §6 Timing「B 轮及以上 → 20-25」
c = score_account("跨境支付公司，B 轮融资，300 人，扩编财务团队，CEO 内推，官网注册试用")
timing = next(d for d in c.dims if d.key == "Timing")
check("B1 B 轮融资 → Timing 满分区 20-25", 20 <= timing.score <= 25,
      f"Timing={timing.score}")

# B2: §6 Timing 只认「B 轮及以上」, A 轮不能拿满分区
c_a = score_account("跨境支付公司，A 轮融资，300 人")
timing_a = next(d for d in c_a.dims if d.key == "Timing")
check("B2 A 轮不占「B 轮及以上」档（≤19）", timing_a.score < 20,
      f"A 轮 Timing={timing_a.score}")

# B3: §6 Timing「无融资但拓新市场 → 15-20」
c_exp = score_account("跨境支付公司，正在扩张东南亚新市场")
timing_e = next(d for d in c_exp.dims if d.key == "Timing")
check("B3 无融资+拓新市场 → Timing 15-20", 15 <= timing_e.score <= 20,
      f"Timing={timing_e.score}")

# B4: §6 Access「每命中一项 +8」
c_one = score_account("跨境支付公司，CEO 可直接联系")
c_three = score_account("跨境支付公司，官网注册试用，老客户内推，CFO 可直接联系")
acc_one = next(d for d in c_one.dims if d.key == "Access")
acc_three = next(d for d in c_three.dims if d.key == "Access")
check("B4a 命中 1 项 Access = 8", acc_one.score == 8, f"Access={acc_one.score}")
check("B4b 命中 3 项 Access = 24 (8×3, 不超上限)", acc_three.score == 24,
      f"Access={acc_three.score}")

# B5: §6 Fit「行业匹配且规模命中 §1 锚点 → 20-25」
c_fit = score_account("跨境支付公司，300 人")
fit = next(d for d in c_fit.dims if d.key == "Fit")
check("B5a 行业匹配 + 300 人（企业层）→ Fit 20-25", 20 <= fit.score <= 25,
      f"Fit={fit.score}")
c_smb = score_account("跨境支付公司，20 人")
fit_smb = next(d for d in c_smb.dims if d.key == "Fit")
check("B5b 行业匹配 + 20 人（未达 50 人锚点）→ Fit 10-15", 10 <= fit_smb.score <= 15,
      f"Fit={fit_smb.score}")

# B6: §6 Intent 两档
c_hire = score_account("跨境公司，正在扩编财务团队")
c_dl = score_account("跨境公司，下载了我们的白皮书")
check("B6a 扩编财务团队 → Intent 15-25",
      15 <= next(d for d in c_hire.dims if d.key == "Intent").score <= 25)
check("B6b 内容下载 → Intent 10-15",
      10 <= next(d for d in c_dl.dims if d.key == "Intent").score <= 15)

# B7: tier 分档边界 (§6: 80-100 Hot / 60-79 Warm / 40-59 Cold / <40 不追)
c_hot = score_account("跨境支付，B 轮，300 人，扩编财务团队，CEO 内推，官网试用")
check("B7a 高分客户 → Hot", c_hot.tier == "Hot",
      f"total={c_hot.total} tier={c_hot.tier}")
c_cold = score_account("跨境支付公司，B 轮融资")
check("B7b 仅 Timing 有分 → 不高于 Warm", c_cold.tier in ("Cold", "Warm", "不追"),
      f"total={c_cold.total} tier={c_cold.tier}")

# ============================================================
# C. 红线优先 —— §7 不追清单无条件压过一切分数
# ============================================================
print("\nC 组 · 红线优先（§7 命中即不追，无例外）")

# 刻意构造一个「分数很高但命中红线」的输入: 若实现把分数当主判据, 这里必挂
RED_Q = "调研一家虚拟货币交易所，B 轮融资，500 人，正在扩编财务团队，CEO 内推，官网试用"
c_red = score_account(RED_Q)
check("C1 红线命中 → tier=不追", c_red.tier == "不追", f"tier={c_red.tier}")
check("C2 红线命中 → 分数仍然照算（可审计，不抹掉证据）", c_red.total > 0,
      f"total={c_red.total}")
check("C3 红线命中 → 标注具体红线条款", bool(c_red.red_lines),
      f"red_lines={c_red.red_lines}")
check("C4 红线命中 → 路由为终止而非推进", "不追" in c_red.route or "合规" in c_red.route,
      f"route={c_red.route}")
check("C5 渲染文本含红线警示", "不追清单" in c_red.as_markdown())

for kw, label in (("线上赌博", "线上赌博"), ("成人内容", "成人内容"),
                  ("OFAC 制裁名单", "制裁"), ("空壳主体", "空壳")):
    check(f"C6 红线关键词「{kw}」被识别",
          any(label in r for r in score_account(f"调研一家{kw}公司").red_lines),
          f"实际={score_account(f'调研一家{kw}公司').red_lines}")

# ============================================================
# D. 缺事实的处理 —— 不给同情分，但也不误判为不追
# ============================================================
print("\nD 组 · 缺事实（记 0、标补充，但不等于「不追」）")

c_none = score_account("调研一下这家公司")
check("D1 四维全缺 → 全部记 0", all(d.score == 0 for d in c_none.dims))
check("D2 四维全缺 → 每个维度标 missing", all(d.missing for d in c_none.dims))
check("D3 缺事实 ≠ 不追（不会误报合规终止）",
      c_none.tier != "不追" and not c_none.disqualified, f"tier={c_none.tier}")
check("D4 缺事实 → insufficient 标志为真", c_none.insufficient)
check("D5 渲染文本明确写「无法评分」而非「不追」",
      "无法评分" in c_none.as_markdown() and "信息不足" in c_none.as_markdown())

# 部分缺事实: 分数必须是「下限」且被显式声明
c_part = score_account("跨境支付公司，B 轮融资")
check("D6 部分缺事实 → 至少一维 missing", len(c_part.missing_dims) >= 1,
      f"missing={c_part.missing_dims}")
check("D7 部分缺事实 → 渲染文本声明当前分为下限",
      "下限" in c_part.as_markdown(), c_part.as_markdown()[-200:])
check("D8 部分缺事实 ≠ insufficient", not c_part.insufficient)

# ============================================================
# E. 依据可追溯 —— 每一分都要能说清出处（可审计性的实质）
# ============================================================
print("\nE 组 · 依据可追溯（公平性要求决策依据可重建）")

c = score_account("跨境支付公司，B 轮融资，300 人，扩编财务团队，CFO 内推")
for d in c.dims:
    check(f"E {d.key} 有依据类型标注", d.basis in ("用户提供事实", "缺事实"),
          f"basis={d.basis}")
    if not d.missing:
        check(f"E {d.key} 有文档条款出处", bool(d.rule.strip()), f"rule={d.rule!r}")
        check(f"E {d.key} 有事实原文", bool(d.evidence.strip()), f"evidence={d.evidence!r}")

check("E9 维度顺序固定为 Fit/Timing/Access/Intent",
      [d.key for d in c.dims] == ["Fit", "Timing", "Access", "Intent"])
check("E10 各维均不超过 25 上限", all(d.score <= 25 for d in c.dims))
check("E11 总分 = 四维之和", c.total == sum(d.score for d in c.dims))
check("E12 总分不超过 100", c.total <= 100)

# ============================================================
# F. 路由联动 —— §2 分层路由与 tier 不能互相矛盾
# ============================================================
print("\nF 组 · 路由联动（§2 双指标取高者）")

c_ae = score_account("跨境支付公司，B 轮，100 人，月跨境付款 500 笔")
check("F1 月付款 > 200 笔 → AE 直跟", "AE" in c_ae.route, f"route={c_ae.route}")
c_self = score_account("跨境支付公司，B 轮，20 人，月跨境付款 10 笔")
check("F2 SMB + 低频 → 自助通道", "自助" in c_self.route, f"route={c_self.route}")
c_red2 = score_account("虚拟货币交易所，B 轮，500 人，月付款 800 笔")
check("F3 红线优先于路由（高分高频也不能推进）", "不追" in c_red2.route,
      f"route={c_red2.route}")
check("F4 tier=信息不足时路由为空（不硬塞建议）",
      score_account("调研一下这家公司").route == "")

# ============================================================
# G. 输入鲁棒性 —— 空输入 / 超长输入不该炸
# ============================================================
print("\nG 组 · 输入鲁棒性")

for bad in ("", "   ", "???", "调研" * 500):
    try:
        cc = score_account(bad)
        ok = isinstance(cc.total, int) and 0 <= cc.total <= 100
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"     异常: {e}")
    check(f"G 异常输入 len={len(bad)} 不崩溃且总分合法", ok)

print("\n" + "=" * 56)
print(f"== 评测结果: {PASS}/{PASS + FAIL} 通过" + (" ✅" if FAIL == 0 else f" ❌ 失败 {FAIL}"))
sys.exit(1 if FAIL else 0)
