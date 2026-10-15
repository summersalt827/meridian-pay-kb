#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账户情报评分卡 —— 把文档里写死的评分规则变成可执行、可复现、可审计的代码。

为什么单独成模块
-----------------
gtm/icp-targeting.md 第六节已经把评分规则写清楚了 (Fit/Timing/Access/Intent
四维各 0-25, 总分 100, tier 映射与 SLA)。但规则写在文档里不等于被执行:
改造前, LLM 路径靠模型自由发挥打分, 离线路径干脆没有分数 —— 而
synth_prompt 向用户承诺了完整的评分卡。

这在 FinTech 语境下不是小瑕疵: 一个给销售用的评分卡, 两次运行给出不同分,
且每一分没有可追溯出处, 与「可审计」这个核心卖点直接冲突。MAS FEAT 的
fairness / accountability 原则要求决策依据可被重建 —— 模型说「85 分」
做不到这一点, 代码说「85 分, 其中 Timing 25 分来自 B 轮融资(规则 §6),
Access 8 分来自内推(规则 §6)」可以。

设计原则 (与 shield.py 一致: 确定性优先于概率)
----------------------------------------------
1. 每个维度的每一分都必须能指回 icp-targeting.md 的具体条款。
2. 缺事实的维度记 0 并显式标「需人工补充」, 不猜、不给同情分。
3. 红线 (不追清单) 优先于一切分数 —— 命中即 tier=不追, 无论总分多高。
4. 同一输入永远得到同一输出, 不引入随机数、不引入时间依赖。

LLM 的角色被降级为「叙述者」: 分数由本模块算出后作为既成事实注入
synth_prompt, 模型负责组织语言, 不能改分。

用法
----
    from scorecard import score_account
    card = score_account("调研 Nium, 东南亚跨境支付, B 轮融资, 300 人")
    card.total, card.tier, card.sla
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Dimension",
    "Scorecard",
    "score_account",
    "ICP_INDUSTRIES",
    "RED_LINE_PATTERNS",
]

# ============================================================
# 锚点常量 —— 与 gtm/icp-targeting.md 第一/二节保持一致
# (改文档就得改这里; AGENTS.md 硬约束 #4 的同一条纪律:
#  标注只存在于代码常量, 不读文档自我声明)
# ============================================================

TIER_ENTERPRISE_HEADCOUNT = 300      # >= 300 人 -> 企业客户层
TIER_MID_HEADCOUNT = 50              # 50-300 人 -> 中端市场
ROUTE_AE_MONTHLY_TXN = 200           # 月跨境付款 > 200 笔 -> AE 直跟
ROUTE_SDR_MONTHLY_TXN = 50           # 50-200 笔 -> SDR 跟进

# 第四节「优先级信号」+ 第一节 ICP 画像里的目标行业
ICP_INDUSTRIES: tuple[str, ...] = (
    "外贸", "出口", "跨境", "物流", "咨询", "软件外包", "连锁零售",
    "出海", "saas", "电商", "制造", "供应链",
)

# 第五节「不接的行业」+ 第七节「不追清单」—— 命中即不追, 无例外
RED_LINE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(虚拟货币|加密货币|数字货币|区块链交易)", "虚拟货币"),
    (r"(线上赌博|网络赌博|博彩)", "线上赌博"),
    (r"(成人内容|色情)", "成人内容"),
    (r"(制裁名单|OFAC|MAS\s*制裁)", "MAS/OFAC 制裁名单主体或关联方"),
    (r"(空壳|无实体经营|壳公司)", "无实体经营的空壳主体"),
    (r"(股东结构不透明|无法完成\s*KYC)", "股东结构不透明 / 无法完成 KYC"),
)

# tier 映射与响应 SLA (第六节)
TIER_BANDS: tuple[tuple[int, int, str, str], ...] = (
    (80, 100, "Hot", "AE 2 小时内响应"),
    (60, 79, "Warm", "SDR 24 小时内首触"),
    (40, 59, "Cold", "进入培育序列或 PARK"),
)

DOC_RULE = "文档规则(gtm/icp-targeting.md §6)"
USER_FACT = "用户提供事实"
MISSING = "缺事实"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Dimension:
    """评分卡的一个维度。"""
    key: str            # Fit / Timing / Access / Intent
    label: str          # 中文展示名
    score: int          # 0-25
    basis: str          # 依据类型: 文档规则 / 用户提供事实 / 缺事实
    rule: str           # 命中的文档条款原文摘要
    evidence: str       # 从输入里抽到的具体事实
    missing: bool = False   # True: 该维度缺事实, 记 0 且需人工补充

    def as_line(self) -> str:
        head = f"- **{self.label}（{self.key}）{self.score}/25**"
        if self.missing:
            return f"{head} — 缺事实，需人工补充（记 0，不给同情分）"
        tail = f"依据：{self.basis}｜{self.rule}"
        if self.evidence:
            tail += f"｜事实：{self.evidence}"
        return f"{head} — {tail}"


@dataclass
class Scorecard:
    dims: list[Dimension] = field(default_factory=list)
    total: int = 0
    tier: str = "不追"
    sla: str = ""
    route: str = ""
    red_lines: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    # 下游槽位填充用的原始事实 (outreach.py 的邮件模板按这些填槽)。
    # 与 dims 同源: 这些是「抽到的事实」, 不是「推断出的结论」,
    # 下游只能引用, 不能反过来改分。
    industry: str = ""
    headcount: int | None = None
    txn: int | None = None
    funding: str = ""
    expansion: str = ""
    access: list[str] = field(default_factory=list)

    @property
    def disqualified(self) -> bool:
        """命中红线 → 不追 (§7 无例外)。"""
        return bool(self.red_lines)

    @property
    def missing_dims(self) -> list[str]:
        return [d.key for d in self.dims if d.missing]

    @property
    def insufficient(self) -> bool:
        """四维全缺事实 —— 这不是「不追」, 是「没法评分」。

        区分这两者是刻意设计: 缺事实却输出 tier=不追, 销售会误以为客户
        被合规否决了, 实际上只是信息没给够。上报合规是红线专属动作,
        不能因为省事把两种终止混为一谈。
        """
        return len(self.missing_dims) == len(self.dims) and not self.red_lines

    def as_markdown(self) -> str:
        """渲染成简报里的「一、公司概况与信号评分卡」小节。"""
        out: list[str] = []
        for d in self.dims:
            out.append(d.as_line())

        if self.red_lines:
            out.append("")
            out.append(
                f"**⛔ 命中不追清单红线（优先于一切分数）：{'、'.join(self.red_lines)}**"
                " — tier=不追，立即终止并上报合规（§7 红线无例外）"
            )
            return "\n".join(out)

        out.append("")
        if self.insufficient:
            out.append(
                "**总分 0/100 → 四维均无事实依据，无法评分**"
                "（这不是「不追」，是信息不足；补充事实后重新评分）"
            )
            out.append("路由建议：待补事实后再定，暂不进入任何序列")
            return "\n".join(out)

        out.append(f"**总分 {self.total}/100 → tier={self.tier}｜SLA：{self.sla}**")
        # 部分维度缺事实时必须声明分数是下限, 否则销售会低估客户
        if self.missing_dims:
            out.append(
                f"⚠️ {'、'.join(self.missing_dims)} 缺事实记 0，"
                f"当前 {self.total} 分为**下限**；补齐事实后分数只会更高，tier 可能上调。"
            )
        if self.route:
            out.append(f"路由建议：{self.route}")
        return "\n".join(out)


# ============================================================
# 信号抽取 —— 全部确定性正则, 不依赖 LLM
# ============================================================

def _headcount(q: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:人|名员工|员工)", q)
    return int(m.group(1)) if m else None


def _monthly_txn(q: str) -> int | None:
    m = re.search(r"(\d+)\s*笔", q)
    return int(m.group(1)) if m else None


def _funding_round(q: str) -> str:
    """返回融资轮次字母 (A/B/C/D/E/种子/天使), 无则空串。"""
    m = re.search(r"(种子|天使|A|B|C|D|E)\s*轮", q)
    return m.group(1) if m else ""


def _expansion(q: str) -> str:
    """新市场 / 新主体信号。"""
    m = re.search(
        r"(东南亚|新加坡|马来西亚|印尼|越南|泰国|菲律宾|出海|海外|新市场|新主体|扩张)", q)
    return m.group(1) if m else ""


def _industry_hit(q: str) -> str:
    for kw in ICP_INDUSTRIES:
        if kw in q.lower():
            return kw
    return ""


def _access_hits(q: str) -> list[str]:
    """§6: 官网自助入口、内推关系、高管可直接联系, 每命中一项 +8。"""
    hits: list[str] = []
    if re.search(r"(官网|自助注册|自助入口|试用|联系我们|提交了表单)", q):
        hits.append("官网自助入口")
    if re.search(r"(内推|朋友介绍|熟人|引荐|推荐我们|老客户介绍)", q):
        hits.append("内推关系")
    if re.search(r"(CEO|CFO|CTO|创始人|联合创始人|财务总监|VP|总监|高管)", q):
        hits.append("高管可直接联系")
    return hits


def _intent_level(q: str) -> tuple[int, str]:
    """§6: 扩编财务/采购团队 15-25; 官网搜索或内容下载 10-15; 无证据 0。"""
    if re.search(r"(扩编|扩招|正在招|招聘|组建|新增).{0,8}(财务|采购|结算|资金|出纳)", q) \
       or re.search(r"(财务|采购|结算).{0,6}(团队|部门).{0,6}(扩|招|组建|新增)", q):
        return 20, "扩编财务/采购团队中"
    if re.search(r"(下载|索取|看了我们的|浏览|搜索|白皮书|案例|咨询过|问过)", q):
        return 12, "官网搜索或内容下载"
    return 0, ""


def _red_lines(q: str) -> list[str]:
    hits: list[str] = []
    for pat, label in RED_LINE_PATTERNS:
        if re.search(pat, q):
            hits.append(label)
    return hits


# ============================================================
# 评分 —— 严格按 §6 条款, 每档写明出处
# ============================================================

def _score_fit(q: str, headcount: int | None, industry: str) -> Dimension:
    """§6 Fit: 行业在 ICP 画像内且规模命中第一节分层锚点 → 20-25;
    行业匹配但规模未达标 → 10-15; 行业不匹配 → 0-5。"""
    label, key = "行业与规模匹配度", "Fit"

    if not industry:
        if headcount is not None and headcount >= TIER_MID_HEADCOUNT:
            # 规模达标但行业未知: 不敢给满分区, 取区间下沿并标行业待确认
            return Dimension(key, label, 15, USER_FACT,
                             "规模命中 §1 分层锚点，行业待确认",
                             f"约 {headcount} 人")
        return Dimension(key, label, 0, MISSING, "行业与规模均无事实", "", missing=True)

    # 行业已匹配, 看规模
    if headcount is None:
        return Dimension(key, label, 15, USER_FACT,
                         f"行业「{industry}」在 ICP 画像内(§5)，规模未确认",
                         f"行业 {industry}")
    if headcount >= TIER_ENTERPRISE_HEADCOUNT:
        ev = f"行业 {industry}，约 {headcount} 人（企业客户层，≥{TIER_ENTERPRISE_HEADCOUNT} 人）"
    elif headcount >= TIER_MID_HEADCOUNT:
        ev = f"行业 {industry}，约 {headcount} 人（中端市场，{TIER_MID_HEADCOUNT}-{TIER_ENTERPRISE_HEADCOUNT} 人）"
    else:
        ev = f"行业 {industry}，约 {headcount} 人"
    hit = headcount >= TIER_MID_HEADCOUNT
    return Dimension(key, label, 25 if hit else 12, USER_FACT,
                     "行业在 ICP 画像内且规模命中 §1 分层锚点 → 20-25"
                     if hit else "行业匹配但规模未达标 → 10-15",
                     ev)


def _score_timing(q: str, funding: str, expansion: str) -> Dimension:
    """§6 Timing: B 轮及以上融资 → 20-25; 无融资但拓新市场或新开主体 → 15-20;
    无时机信号 → 0-5。"""
    label, key = "时机信号", "Timing"

    if funding in ("B", "C", "D", "E"):
        return Dimension(key, label, 25, USER_FACT,
                         "B 轮及以上融资 → 20-25", f"{funding} 轮融资")
    if funding:
        # 种子/天使/A 轮: 文档只认「B 轮及以上」, 早期轮次不给满分区
        return Dimension(key, label, 12, USER_FACT,
                         f"{funding} 轮未达「B 轮及以上」门槛 → 不计入 20-25 档",
                         f"{funding} 轮融资")
    if expansion:
        return Dimension(key, label, 17, USER_FACT,
                         "无融资但拓新市场 / 新开主体 → 15-20", f"扩张信号：{expansion}")
    return Dimension(key, label, 0, MISSING, "无时机信号", "", missing=True)


def _score_access(q: str, hits: list[str]) -> Dimension:
    """§6 Access: 官网自助入口、内推关系、高管可直接联系, 每命中一项 +8。"""
    label, key = "触达路径", "Access"
    if not hits:
        return Dimension(key, label, 0, MISSING, "无触达路径", "", missing=True)
    # 文档写「每命中一项 +8」, 三项全中 = 24, 不超过 25 上限
    return Dimension(key, label, min(8 * len(hits), 24), USER_FACT,
                     "每命中一项 +8（§6）", "、".join(hits))


def _score_intent(q: str) -> Dimension:
    """§6 Intent: 扩编财务/采购团队中 → 15-25; 官网搜索或内容下载 → 10-15;
    无意图证据 → 0。"""
    label, key = "意图信号", "Intent"
    score, ev = _intent_level(q)
    if score == 0:
        return Dimension(key, label, 0, MISSING, "无意图证据", "", missing=True)
    rule = "扩编财务/采购团队中 → 15-25" if score == 20 else "官网搜索或内容下载 → 10-15"
    return Dimension(key, label, score, USER_FACT, rule, ev)


def _route_for(q: str, headcount: int | None, txn: int | None, tier: str) -> str:
    """§2 分层路由: 双指标取高者为准, 不允许人工抢占。"""
    if tier == "不追":
        return "不追 / 上报合规"
    if txn is not None and txn > ROUTE_AE_MONTHLY_TXN:
        return f"AE 直跟（月跨境付款 > {ROUTE_AE_MONTHLY_TXN} 笔，24 小时内首触）"
    if headcount is not None and headcount >= TIER_ENTERPRISE_HEADCOUNT:
        return "AE 直跟（企业客户层，多干系人推进）"
    if txn is not None and txn >= ROUTE_SDR_MONTHLY_TXN:
        return f"SDR 跟进（{ROUTE_SDR_MONTHLY_TXN}-{ROUTE_AE_MONTHLY_TXN} 笔，初筛后转 AE）"
    if headcount is not None and headcount >= TIER_MID_HEADCOUNT:
        return "SDR 跟进（中端市场，约会议 + 场景化演示）"
    if headcount is not None or txn is not None:
        return "自助通道（SMB，内容驱动 + 自助转化）"
    return ""


def score_account(question: str) -> Scorecard:
    """主入口: 输入调研请求原文, 输出确定性评分卡。

    同一输入永远得到同一输出 —— 无随机、无时间依赖、无网络调用。
    """
    q = (question or "").strip()
    headcount = _headcount(q)
    txn = _monthly_txn(q)
    funding = _funding_round(q)
    expansion = _expansion(q)
    industry = _industry_hit(q)
    access = _access_hits(q)
    reds = _red_lines(q)

    dims = [
        _score_fit(q, headcount, industry),
        _score_timing(q, funding, expansion),
        _score_access(q, access),
        _score_intent(q),
    ]
    total = sum(d.score for d in dims)

    card = Scorecard(dims=dims, total=total, red_lines=reds,
                     industry=industry, headcount=headcount, txn=txn,
                     funding=funding, expansion=expansion, access=access)

    # 信号摘要 (沿用原 _parse_account_signals 的口径, 保持输出连续)
    if headcount is not None:
        if headcount >= TIER_ENTERPRISE_HEADCOUNT:
            t = f"企业客户层（≥{TIER_ENTERPRISE_HEADCOUNT} 人）"
        elif headcount >= TIER_MID_HEADCOUNT:
            t = f"中端市场（{TIER_MID_HEADCOUNT}-{TIER_ENTERPRISE_HEADCOUNT} 人）"
        else:
            t = f"SMB（<{TIER_MID_HEADCOUNT} 人）"
        card.signals.append(f"规模信号：约 {headcount} 人 → {t}")
    if txn is not None:
        card.signals.append(f"用量信号：月均 {txn} 笔跨境付款")
    if funding:
        card.signals.append(f"资本信号：{funding} 轮融资")
    if expansion:
        card.signals.append(f"扩张信号：{expansion} → 新市场 / 新主体（优先级信号）")

    # 红线优先于一切分数
    if reds:
        card.tier = "不追"
        card.sla = "立即终止并上报合规（§7 红线无例外）"
        card.route = "不追 / 上报合规"
        return card

    if card.insufficient:
        # 四维全缺事实: 不给 tier, 也不进不追序列 (见 Scorecard.insufficient)
        card.tier, card.sla = "信息不足", "补充事实后重新评分"
        card.route = ""
        return card

    # 有维度缺事实时, total 是下限而非结论 —— 不能拿下限做终结性判定。
    # 否则会出现自相矛盾: 上面刚写了「补齐事实后 tier 可能上调」,
    # 下面却因为下限不足 40 就把客户判成「不追」。语义上这两种终止完全不同:
    #   - 事实齐全但确实低分  → 不追 (这是判断)
    #   - 事实不全导致低分    → 待补事实 (这只是没问清楚)
    lowered = bool(card.missing_dims)
    for lo, hi, tier_name, sla in TIER_BANDS:
        if lo <= total <= hi:
            card.tier, card.sla = tier_name, sla
            break
    else:
        if lowered:
            card.tier = "待补事实"
            card.sla = "当前分为下限，补齐缺事实维度后重新判定"
        else:
            card.tier, card.sla = "不追", "低于 40 分 → 不追"
    card.route = _route_for(q, headcount, txn, card.tier)
    return card


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "调研东南亚跨境支付公司，B 轮融资，300 人，正在扩编财务团队，CFO 内推"
    c = score_account(q)
    print(f"问题: {q}\n")
    print(c.as_markdown())
    print(f"\n[调试] 红线={c.red_lines} 信号={len(c.signals)}")
