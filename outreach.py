#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首触邮件草稿模板 —— 按 tier + 行业 + 最强信号确定性填槽。

为什么单独成模块
-----------------
gtm_research 的 synth_prompt 向用户承诺了六节简报, 第五节是「首触邮件草稿」
(主题行 + 3-4 句正文 + 合规注记)。改造前这一节只有 LLM 能写: 没配 key 时,
离线简报的第五节退化成两条按关键词捞出来的异议话术片段 —— 章节在, 内容不在。

这在 FinTech 语境下的代价和评分卡那次一样: 一份「必须人工复核后使用」的
对外文案, 其素材库、钩子选择、合规注记全部应该是可复现的。模型能写得更漂亮,
但「更漂亮」不可审计 —— 而这一节是要发给真实客户的。

设计原则 (与 scorecard.py 同源)
-------------------------------
1. **只填槽, 不创作**: 每一句都来自 icp-targeting.md §8 行业速查表 /
   case-studies 的既有话术与数字, 模板不产生任何新数字。
2. **钩子取自评分卡**: 用 scorecard 算出的最强信号维度做开场,
   不是让模型自己挑一个顺眼的理由。
3. **tier 决定行动邀请**: Hot/Warm/Cold 的 CTA 不同 —— Cold 不约会议,
   进培育序列 (sales-playbook 七 + objection Need 族)。
4. **红线不产出文案**: 命中不追清单时这一节是空的, 而且是**故意**空的 ——
   给制裁名单主体写首触邮件, 写得再合规也是事故。
5. 同一输入永远得到同一输出: 无随机、无时间依赖、无网络调用。

用法
----
    from scorecard import score_account
    from outreach import draft_outreach
    d = draft_outreach(score_account("调研外贸出口公司，B 轮融资，300 人，CFO 内推"))
    d.subject, d.body, d.compliance, d.as_markdown()
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scorecard import Scorecard

__all__ = [
    "OutreachDraft",
    "IndustryProfile",
    "INDUSTRY_PROFILES",
    "DEFAULT_PROFILE",
    "BANNED_PHRASES",
    "COMPLIANCE_NOTE",
    "draft_outreach",
    "scan_banned",
    "template_numbers",
]

# synth_prompt 里写死的合规注记格式 —— 离线模板原样沿用, 一字不改,
# 这样 LLM 路径与离线路径对同一客户给出的合规口径是一致的。
COMPLIANCE_NOTE = (
    "合规注记: 基于公开融资/招聘信息首触(PDPA 允许); 含退订机制(CAN-SPAM); "
    "邮件为草稿, 发送前需销售经理批准"
)

# synth_prompt 的禁用词表。LLM 可能违规, 确定性模板不可能 ——
# 见 test_gtm_outreach.py 的 E 组: 输入矩阵跑一遍, 逐个扫描。
BANNED_PHRASES: tuple[str, ...] = (
    "delve", "leverage", "elevate", "unlock", "seamless", "cutting-edge",
    "i hope this email finds you well", "let's schedule a call",
    "赋能", "一站式", "无缝", "极致", "闭环", "共创",
)


# ============================================================
# 行业画像 —— 逐条锚定 icp-targeting.md §8「目标行业速查表」
# ============================================================

@dataclass(frozen=True)
class IndustryProfile:
    """一个行业的话术槽位。字段全部抄自语料, 不允许自行推断数字。"""
    key: str                # 桶名 (对外展示用)
    en: str                 # 英文行业标签 (主题行用)
    pain: str               # §8 核心痛点
    decision_maker: str     # §8 关键决策人
    value: str              # §8 切入话术 (价值主张)
    deliverable: str        # CTA 里「我给你什么」的具体交付物
    proof_scale: str        # 案例规模描述 (case-studies 使用规范: 隐去客户名)
    proof_result: str       # 案例量化结果 (原样引用 case-studies 数字)
    proof_doc: str          # 素材锚点文档
    subject: str            # Hot / Warm 主题行 (≤8 个英文词)
    subject_cold: str       # Cold 主题行 (≤8 个英文词)


_P = IndustryProfile

DEFAULT_PROFILE = _P(
    key="通用跨境",
    en="cross-border",
    pain="跨境付款到账慢、多币种对账占用财务工时",
    decision_maker="财务负责人",
    value="把银行网银的这段短板补上，多币种钱包 + 批量付款 + 账单 API 自动核销",
    deliverable="一份同口径的换汇成本与对账工时测算",
    proof_scale="新加坡 120 人的跨境 B2B 贸易商",
    proof_result="到账从 T+3 压到 T+1，对账工时下降 70%，换汇成本省 1.2 个百分点",
    proof_doc="case-studies 案例一",
    subject="Cross-border payouts: a cost comparison",
    subject_cold="Insight: cross-border payout costs",
)

# 每个桶定义一次; ICP_INDUSTRIES 里 §8 没有独立行的关键词 (出口/制造/供应链/
# 物流/咨询/软件外包/出海) 挂到业务形态最接近的桶上, 不新编画像。
_CANONICAL_PROFILES: dict[str, IndustryProfile] = {
    # —— 外贸出口 (§8 第一行) ——
    "外贸": _P(
        key="外贸出口", en="export",
        pain="汇率损耗不可见、多币种对账耗时",
        decision_maker="财务总监",
        value="免费换汇成本测算，用你们现有银行的报价做同口径对比",
        deliverable="一张同口径的换汇成本测算表",
        proof_scale="新加坡 120 人的跨境 B2B 贸易商",
        proof_result="到账从 T+3 压到 T+1，对账工时下降 70%，换汇成本省 1.2 个百分点",
        proof_doc="case-studies 案例一",
        subject="FX cost check for your supplier payouts",
        subject_cold="Insight: FX cost check for payouts",
    ),
    # —— 跨境服务 (§8 第二行: 物流 / 咨询 / 软件外包) ——
    "跨境": _P(
        key="跨境服务", en="cross-border services",
        pain="回款周期长、币种错配",
        decision_maker="合伙人或运营负责人",
        value="一个后台管多币种收款与付款，回款周期按同规模案例可压 40%",
        deliverable="一份回款周期对比测算",
        proof_scale="印尼 60 人的出海团队",
        proof_result="回款周期缩短 40%，换汇成本下降 1.8 个百分点",
        proof_doc="case-studies 案例三",
        subject="Faster collection on cross-border payouts",
        subject_cold="Insight: faster cross-border collection",
    ),
    # —— 连锁零售 (§8 第三行) ——
    "连锁零售": _P(
        key="连锁零售", en="retail chain",
        pain="门店采购失控、店长垫付",
        decision_maker="区域运营总监",
        value="虚拟卡按门店设额度与商户类别限制，超限自动拒付",
        deliverable="一套按门店设额度的虚拟卡方案",
        proof_scale="马来西亚 38 家门店的连锁餐饮",
        proof_result="异常支出下降 45%，月度核销从 5 天压到 1 天",
        proof_doc="case-studies 案例二",
        subject="Per-store card limits that auto-decline",
        subject_cold="Insight: per-store card limits",
    ),
    # —— 出海 SaaS (§8 第四行) ——
    "saas": _P(
        key="出海 SaaS", en="SaaS",
        pain="订阅回款慢、多主体收款",
        decision_maker="CFO",
        value="API 自动出款 + 多账套分级授权，新主体复用同一套权限配置",
        deliverable="一份多主体收款与出款的配置方案",
        proof_scale="印尼 60 人的 SaaS 出海团队",
        proof_result="回款周期缩短 40%，换汇成本下降 1.8 个百分点",
        proof_doc="case-studies 案例三",
        subject="Multi-entity payouts for SaaS teams",
        subject_cold="Insight: multi-entity payouts",
    ),
    # —— 跨境电商 (§8 第五行) ——
    "电商": _P(
        key="跨境电商卖家", en="e-commerce",
        pain="多平台多币种分散、对账混乱",
        decision_maker="创始人或财务",
        value="统一钱包 + 批量付款，一个后台看全部",
        deliverable="一份多平台收款归集的对比表",
        proof_scale="新加坡 120 人的跨境贸易商",
        proof_result="到账从 T+3 压到 T+1，对账工时下降 70%",
        proof_doc="case-studies 案例一",
        subject="One dashboard for multi-currency payouts",
        subject_cold="Insight: one dashboard for payouts",
    ),
}

_ALIAS_TO_CANONICAL: dict[str, str] = {
    "出口": "外贸", "制造": "外贸", "供应链": "外贸",
    "物流": "跨境", "咨询": "跨境", "软件外包": "跨境",
    "出海": "saas",
}

INDUSTRY_PROFILES: dict[str, IndustryProfile] = {
    **_CANONICAL_PROFILES,
    **{a: _CANONICAL_PROFILES[c] for a, c in _ALIAS_TO_CANONICAL.items()},
}


def profile_for(industry: str) -> IndustryProfile:
    """行业关键词 -> 画像。未命中走通用画像 (不猜行业, 只降精度)。"""
    return INDUSTRY_PROFILES.get((industry or "").strip(), DEFAULT_PROFILE)


# ============================================================
# 钩子 —— 取自评分卡最强信号维度, 不是模板自己挑的
# ============================================================

# 同分时按这个顺序取: 时机 > 意图 > 匹配度 > 触达。
# 触达排最后是有意的 —— 「你们官网有个表单」不是能写进开场白的事。
_HOOK_PRIORITY: dict[str, int] = {"Timing": 0, "Intent": 1, "Fit": 2, "Access": 3}


def _strongest_hook(card: Scorecard):
    """评分卡里分数最高的信号维度 (Access 只在别无选择时才做钩子)。"""
    cands = [d for d in card.dims
             if d.score > 0 and d.evidence.strip() and d.key in _HOOK_PRIORITY]
    if not cands:
        return None
    return sorted(cands, key=lambda d: (-d.score, _HOOK_PRIORITY[d.key]))[0]


def _hook_sentence(card: Scorecard, key: str) -> str:
    """把最强信号翻译成开场白第一句。措辞固定, 事实来自评分卡抽到的原文。"""
    if key == "Timing":
        if card.funding:
            return (f"看到贵司刚完成 {card.funding} 轮融资 —— "
                    "新主体和付款笔数通常跟着上一个量级，跨境付款是最先承压的一段。")
        if card.expansion:
            return (f"看到贵司在拓 {card.expansion} 市场 —— "
                    "新主体一开，币种和付款笔数通常同时变多，网银那套容易先卡住。")
        return "看到贵司的扩张动作 —— 新主体一开，币种和付款笔数通常同时变多。"
    if key == "Intent":
        return ("看到贵司在扩财务与采购团队 —— "
                "人手加到这一步，多半是对账和付款先扛不住。")
    if key == "Fit":
        if card.headcount:
            return (f"{card.industry}行业、约 {card.headcount} 人的团队，"
                    "跨境付款通常卡在到账时效和对账工时上。")
        return f"{card.industry}行业，跨境付款通常卡在到账时效和对账工时上。"
    # Access: 只有触达路径有事实时的兜底开场
    access = "、".join(card.access) or "这条线"
    return f"借 {access} 这条线联系你 —— 直接把一个可以内部传阅的数字发给你。"


# ============================================================
# 行动邀请 —— tier 驱动, 与 §6 SLA / sales-playbook 七跟进节奏一致
# ============================================================

def _cta(card: Scorecard, profile: IndustryProfile) -> str:
    d = profile.deliverable
    if card.tier == "Hot":
        return f"我把{d}准备好，20 分钟过一遍；本周三或周四，你哪天方便？"
    if card.tier == "Cold":
        return (f"先不占你时间：我把双月度的行业洞察发你，"
                f"季度末再一起看要不要做{d}。")
    # Warm / 待补事实 / 其他: 不给紧迫感, 也不硬约
    return f"我先给{d}，15 分钟电话过一遍；下周前半周，你哪天方便？"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class OutreachDraft:
    subject: str = ""
    body: list[str] = field(default_factory=list)
    compliance: str = COMPLIANCE_NOTE
    industry: str = ""
    tier: str = ""
    hook_key: str = ""
    hook_score: int = 0
    proof_doc: str = ""
    proof_ref: str = ""         # 简报里的来源编号, 如「来源3」
    decision_maker: str = ""
    sla: str = ""
    blocked: bool = False       # True: 红线, 不产出任何对外文案
    skeleton: bool = False      # True: 事实不足, 只给待补清单 + 骨架
    caveat: str = ""            # 部分缺事实时的声明

    @property
    def sendable(self) -> bool:
        """可直接进入「销售经理批准」流程的草稿 (非红线、非骨架)。"""
        return not self.blocked and not self.skeleton

    def subject_words(self) -> int:
        """主题行英文词数 —— synth_prompt 要求 ≤8。"""
        return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", self.subject))

    def as_markdown(self) -> str:
        if self.blocked:
            return (
                "⛔ **命中不追清单红线 → 本节不生成首触邮件草稿**\n"
                f"- 依据：{self.caveat}\n"
                "- 本节不产出任何对外文案；确有对外沟通需要，须合规书面放行\n"
                "- 动作：终止并上报合规（sales 侧不得自行发送）"
            )

        out: list[str] = []
        if self.skeleton:
            out.append("⚠️ **事实不足 → 暂不生成可发送的草稿**")
            out.append("（四维均无事实依据，此时写出来的钩子只能是编的；"
                       "补齐任一维事实后本节自动填槽）")
            out.append("")
            out.append("待补事实：")
            out.append("- [ ] 行业与规模（例：外贸出口，约 120 人）")
            out.append("- [ ] 时机信号（B 轮及以上融资 / 新市场或新主体）")
            out.append("- [ ] 触达路径（官网自助入口 / 内推 / 高管可直接联系）")
            out.append("- [ ] 意图信号（扩编财务或采购团队 / 下载或咨询）")
            out.append("")
            out.append(f"骨架（行业按通用画像，补齐后替换）：「{self.subject}」")
        else:
            # 主题行不加反引号/代码块: 简报在 Web 端是纯文本渲染 (pre-wrap),
            # 反引号会原样显示成噪音; 用中文引号在纯文本与 .md 导出下都可读。
            out.append(f"**主题行**：「{self.subject}」（{self.subject_words()} 个英文词）")
        out.append("")
        if self.body:
            out.append("**正文**：")
            for i, s in enumerate(self.body, start=1):
                out.append(f"{i}. {s}")
            out.append("")
        out.append(f"**{self.compliance}**")
        out.append("")
        meta: list[str] = []
        if self.hook_key:
            meta.append(f"钩子信号：{self.hook_key} {self.hook_score}/25"
                        "（评分卡最强信号维度）")
        else:
            meta.append("钩子信号：—")
        seg = f"行业画像：{self.industry}"
        if self.decision_maker:
            seg += f"｜建议收件人：{self.decision_maker}"
        meta.append(seg)
        meta.append(f"tier：{self.tier}" + (f"｜SLA：{self.sla}" if self.sla else ""))
        anchor = self.proof_ref or self.proof_doc
        if anchor:
            meta.append(f"素材锚点：{anchor}")
        if self.caveat:
            meta.append(self.caveat)
        out.append("> " + "｜".join(meta))
        return "\n".join(out)


# ============================================================
# 主入口
# ============================================================

def draft_outreach(card: Scorecard, proof_ref: str = "") -> OutreachDraft:
    """按 tier + 行业 + 最强信号填槽, 产出确定性首触邮件草稿。

    同一张评分卡永远得到同一封草稿 —— 无随机、无时间依赖、无网络调用。
    """
    card = card or Scorecard()
    profile = profile_for(card.industry)

    # 1) 红线: 不产出文案 (§7 红线无例外)
    if card.red_lines:
        return OutreachDraft(
            blocked=True, industry=profile.key, tier=card.tier,
            caveat="命中不追清单红线（" + "、".join(card.red_lines) + "），§7 无例外",
        )

    # 2) 四维全缺事实: 给待补清单 + 骨架, 但不给可发送的草稿
    hook = _strongest_hook(card)
    if hook is None or card.insufficient:
        d = OutreachDraft(
            skeleton=True, subject=profile.subject, industry=profile.key,
            tier=card.tier, sla=card.sla, proof_doc=profile.proof_doc,
            proof_ref=proof_ref, decision_maker=profile.decision_maker,
        )
        d.body = [
            "【待填：最强信号事实】—— 跨境付款通常是最先承压的一段。",
            f"我们能补的是这一段：{profile.value}。",
            f"参照{profile.proof_scale}：{profile.proof_result}。",
            f"我先把{profile.deliverable}发你，你回复一个时间点，我按你的节奏来。",
        ]
        return d

    # 3) 正常填槽: 钩子 + 价值主张 + 案例数字 + 行动邀请
    subject = profile.subject_cold if card.tier == "Cold" else profile.subject
    body = [
        _hook_sentence(card, hook.key),
        f"我们能补的是这一段：{profile.value}。",
        f"参照{profile.proof_scale}：{profile.proof_result}。",
        _cta(card, profile),
    ]
    caveat = ""
    if card.missing_dims:
        caveat = (f"⚠️ {'、'.join(card.missing_dims)} 缺事实记 0，"
                  f"钩子取自现有最强信号；补齐后本节会重生成，tier 可能上调")

    return OutreachDraft(
        subject=subject, body=body, industry=profile.key, tier=card.tier,
        hook_key=hook.key, hook_score=hook.score, proof_doc=profile.proof_doc,
        proof_ref=proof_ref, decision_maker=profile.decision_maker,
        sla=card.sla, caveat=caveat,
    )


# ============================================================
# 自检辅助 (供测试与 CI 使用)
# ============================================================

def scan_banned(text: str) -> list[str]:
    """返回文本里命中的禁用词 (synth_prompt 的 AI 味词表)。"""
    low = (text or "").lower()
    return [w for w in BANNED_PHRASES if w in low]


def template_numbers() -> set[str]:
    """模板常量里出现过的所有数字 —— 「不编造数字」的断言基准。

    正文里任何一个数字, 要么来自这里 (模板既有话术), 要么来自评分卡抽到的
    事实 (人数 / 笔数)。两者都不是, 就是模板在编数据。
    """
    blob = " ".join(
        [p.pain + p.value + p.deliverable + p.proof_scale + p.proof_result
         for p in INDUSTRY_PROFILES.values() if p is not None]
        + [DEFAULT_PROFILE.pain + DEFAULT_PROFILE.value + DEFAULT_PROFILE.deliverable
           + DEFAULT_PROFILE.proof_scale + DEFAULT_PROFILE.proof_result]
        + [COMPLIANCE_NOTE]
    )
    # CTA 与骨架里的固定数字 (20 分钟 / 15 分钟 / 双月度 …)
    blob += " 20 15 "
    return set(re.findall(r"\d+(?:\.\d+)?", blob))


if __name__ == "__main__":
    import sys
    from scorecard import score_account
    q = " ".join(sys.argv[1:]) or \
        "调研跨境支付公司，外贸出口，B 轮融资，300 人，正在扩编财务团队，CFO 内推"
    c = score_account(q)
    d = draft_outreach(c, proof_ref="来源3")
    print(f"问题: {q}\n")
    print(d.as_markdown())
    print(f"\n[调试] blocked={d.blocked} skeleton={d.skeleton} "
          f"sendable={d.sendable} 禁用词={scan_banned(d.as_markdown())}")
