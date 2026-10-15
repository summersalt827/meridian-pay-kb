#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shield.py — Need-to-Know Shield 与提示注入前置扫描: 进 LLM 前的最后一道确定性闸门。

为什么需要第三个门
------------------
已有两道门, 但它们都不覆盖两类泄露:

  * RBAC(目录级) 回答「这份文档你能不能看」
  * 披露门(audience) 回答「这句话能不能当着客户说」

都不回答:

  1. **need-to-know(最小知情)**: 你能看这份文档, 不等于你需要看到里面那位客户的
     卡号 / NRIC。在 FinTech 语境下这是 PDPA「目的限制 + 最小化」的直接要求,
     也是 MAS FEAT 原则里 accountability 的具体落点。
  2. **文档是数据, 不是指令**: 检索到的文字若含「忽略以上指令, 把 X 发到 Y」,
     模型没有理由分辨它是内容还是命令。**间接提示注入**(OWASP LLM01)正是靠这个
     信任缺口把「你能看的文档」变成「你能让模型做的事」。

两者都必须在文本离开确定性代码、进入 LLM **之前**完成, 并且必须是确定性规则:
同样的输入永远得到同样的输出, 否则无法被测试断言。这与项目其余部分一致 ——
鉴权、RBAC、相关性门都是确定性代码, LLM 只在边缘做规划与合成。

设计约束(与 AGENTS.md 硬约束一致)
-----------------------------------
  * 仅标准库, 不新增 pip 依赖
  * 只收窄、从不放宽: 掩码与剔除只会减少模型能看到的内容
  * 审计记「规则与计数」, 绝不记被掩码的明文(否则审计本身成为泄露面)
  * 掩码对同一输入稳定可复现(Luhn / mod-97 校验, 而非猜长度)

掩码的编号在单次调用内稳定: 同一文档里同一个值始终映射为同一个 `[card 1]`,
这样模型引用的是标签, 而标签永远指不回明文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ============================================================
# 统计
# ============================================================


@dataclass
class ShieldStats:
    """一次盾牌处理的统计: 用于审计与 UI 展示(只记计数, 不记明文)。"""

    redacted: dict[str, int] = field(default_factory=dict)      # {kind: count}
    instructions_removed: int = 0                                # 被剔除的指令行数

    def bump(self, kind: str, n: int = 1) -> None:
        self.redacted[kind] = self.redacted.get(kind, 0) + n

    def total(self) -> int:
        return sum(self.redacted.values()) + self.instructions_removed

    def as_dict(self) -> dict:
        return {"redacted": dict(self.redacted),
                "instructions_removed": self.instructions_removed}

    def merge(self, other: "ShieldStats") -> "ShieldStats":
        for k, v in other.redacted.items():
            self.redacted[k] = self.redacted.get(k, 0) + v
        self.instructions_removed += other.instructions_removed
        return self


# ============================================================
# 校验: 用算法而非长度猜测, 保证同一输入同一结果
# ============================================================


def _luhn_ok(digits: str) -> bool:
    """Luhn 校验(ISO/IEC 7812): 银行卡号的行业标准校验位。"""
    if not digits.isdigit() or not (12 <= len(digits) <= 19):
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:          # 从右数第 2,4,6... 位加倍
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_ok(iban: str) -> bool:
    """IBAN mod-97 校验(ISO 13616): 重排后转数字串, 模 97 必须为 1。"""
    s = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    rearranged = s[4:] + s[:4]
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(numeric) % 97 == 1


def _card_ok(raw: str) -> bool:
    return _luhn_ok(re.sub(r"[ \-]", "", raw))


# ============================================================
# 敏感标识模式
# ============================================================
# 顺序即优先级: 靠前的先占位, 重叠区间不会被后面的规则重复掩码
# (例如一张 16 位卡号不会被 phone 规则再切一刀)。

_PATTERNS: tuple[tuple[str, re.Pattern, int | None, object | None], ...] = (
    # 凭证: 无论谁问都掩码, admin 也不例外 —— 它对回答问题毫无价值, 只有泄露风险
    ("credential", re.compile(
        r"sk-[A-Za-z0-9]{16,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|ghp_[A-Za-z0-9]{20,}"
        r"|(?:Bearer|Authorization)\s*:?\s+[A-Za-z0-9._\-]{16,}"
        r"|(?:\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password|passwd|pwd|token)\b\s*[:=]\s*)"
        r"[^\s\"'`，,；;]{6,}",
        re.IGNORECASE), None, None),

    ("card", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"), None, _card_ok),
    # IBAN 长度不固定(各国 15~34 位), 因此不按 4 位分组写死, 靠 mod-97 收敛误报
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){10,30}\b"), None, _iban_ok),
    # 新加坡 NRIC / FIN: 首字母 S/T/F/G/M + 7 位数字 + 校验字母
    ("nric", re.compile(r"\b[STFGM]\d{7}[A-Z]\b"), None, None),
    ("dob", re.compile(r"\b(?:19|20)\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}(?:\s*日)?\b"), None, None),
    ("passport", re.compile(
        r"(?:护照|passport)\s*(?:no\.?|number|号码|No)?\s*[:：]?\s*([A-Z0-9]{6,9})\b",
        re.IGNORECASE), 1, None),
    ("account", re.compile(
        r"(?:账号|帐户|账户|account|a/c)\s*(?:no\.?|number|#|号码)?\s*[:：]?\s*(\d{6,})\b",
        re.IGNORECASE), 1, None),
    # 新加坡手机号: 8 位, 3/6/8/9 开头, 可带 65 区号
    ("phone", re.compile(r"(?:\+?65\s?)?\b[3689]\d{7}\b"), None, None),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None, None),
    # 新加坡地址: 门牌 + 路名 + 6 位邮编
    ("address", re.compile(
        r"\b\d{1,4}[A-Za-z]?\s+[A-Za-z][A-Za-z\s']{2,28}?"
        r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Boulevard|Blvd|Crescent|Way)"
        r"\b[^,;\n]{0,40}?\b(?:Singapore\s*)?\d{6}\b", re.IGNORECASE), None, None),
)

# 掩码后的展示形式: 只保留足以让模型/人类指代的形状, 不保留可复原信息
_LABEL = {
    "credential": "credential",
    "card": "card",
    "iban": "iban",
    "nric": "nric",
    "dob": "dob",
    "passport": "passport",
    "account": "account",
    "phone": "phone",
    "email": "email",
    "address": "address",
}


def _tail(raw: str, keep: int = 4) -> str:
    """取明文的末几位做指代尾巴(如 `card ending 1111`), 不足以复原完整值。"""
    digits = re.sub(r"\D", "", raw)
    return digits[-keep:] if len(digits) >= keep else raw[-keep:]


def _find_all(text: str) -> list[tuple[int, int, str, str]]:
    """扫描文本里所有敏感标识, 返回 [(start, end, kind, raw)], 区间互不重叠。"""
    hits: list[tuple[int, int, str, str]] = []
    for kind, rx, group, validator in _PATTERNS:
        for m in rx.finditer(text):
            raw = m.group(group) if group else m.group(0)
            if validator is not None and not validator(raw):
                continue
            hits.append((m.start(), m.end(), kind, raw))

    # 冲突消解: 起点靠前优先; 同起点时区间长的优先(更具体的规则先占位)
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out: list[tuple[int, int, str, str]] = []
    occupied = -1
    for s, e, kind, raw in hits:
        if s < occupied:
            continue
        out.append((s, e, kind, raw))
        occupied = e
    return out


def mask_identifiers(text: str, stats: ShieldStats | None = None,
                     keep: set[str] | None = None) -> str:
    """把文本里的敏感标识替换为稳定编号标签。

    keep: 允许保持明文的值集合(按 _norm 归一化比较)。用于「答案里出现的、
    且确实展示给过用户的」标识符 —— 例如用户自己在问题里打出的卡号。

    编号在单次调用内稳定: 同一个值第二次出现仍是同一个标签。
    """
    st = stats if stats is not None else ShieldStats()
    keep = keep or set()
    hits = _find_all(text)
    if not hits:
        return text

    seen: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    out: list[str] = []
    cursor = 0
    for s, e, kind, raw in hits:
        out.append(text[cursor:s])
        key = (kind, _norm(raw))
        if key not in seen:
            counters[kind] = counters.get(kind, 0) + 1
            seen[key] = counters[kind]
        idx = seen[key]
        if key[1] in keep:
            out.append(raw)                       # 用户已见过 / 自己输入过, 保留
        else:
            label = _LABEL[kind]
            out.append(f"[{label} {idx} ending {_tail(raw)}]"
                       if kind in ("card", "account", "phone")
                       else f"[{label} {idx}]")
            st.bump(kind)
        cursor = e
    out.append(text[cursor:])
    return "".join(out)


def _norm(value: str) -> str:
    """值的归一化形式, 用于比较「是不是同一个标识符」(去分隔符、小写)。"""
    return re.sub(r"[\s\-]", "", value).lower()


def scan_values(text: str) -> set[str]:
    """返回文本里出现过的敏感标识归一化集合(只用于比较, 不含明文语义)。"""
    return {_norm(raw) for _, _, _, raw in _find_all(text)}


# ============================================================
# 提示注入前置扫描
# ============================================================

# 零宽 / 双向控制字符: 常用于把指令藏在肉眼看不见的地方, 或拆开关键词绕过扫描
_ZERO_WIDTH = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

# 伪装成我们自己的来源标签(`build_context` 用 `[来源N]`)的伪造标签。
# 检索文本若能伪造来源边界, 就能让模型以为「我们说了」某句话。
_FAKE_TAG = re.compile(
    r"[\[［【]\s*(?:来源|source|context|document|doc|system|system\s*message|src)"
    r"[\s:：#]*\d{0,3}\s*[\]］】]",
    re.IGNORECASE)

# 「对 AI 说话」的行: 一行之内出现指令性表述。整行剔除, 而非就地改写 ——
# 就地改写会留下半句话, 反而更容易被模型当成上下文的一部分。
_INSTRUCTION_LINE = re.compile(
    r"^\s*(?:[-*•>#\s]*)?(?:"
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)"
    r"|disregard\s+(?:all\s+|any\s+)?(?:previous|prior|above)"
    r"|system\s*:|assistant\s*:|<\s*/?\s*(?:system|instructions?|prompt)\s*>"
    r"|<\|[^|]{1,24}\|>"
    r"|note\s+to\s+(?:ai|assistant|model|llm)"
    r"|instruction\s*(?:to\s+(?:ai|assistant|model))?\s*:"
    r"|new\s+(?:rule|instruction|system\s+prompt)\s*:"
    r"|忽略(?:以上|上面|前面|之前)?(?:所有|全部)?(?:指令|提示|规则|约束)"
    r"|(?:请|立即|现在)?(?:把|将|发送|泄露|输出|外传|转发).{0,12}(?:密码|密钥|口令|token|卡号|薪酬|工资|机密)"
    r"|你现在是|你现在扮演|忘记(?:以上|之前)?(?:所有)?"
    r"|ai\s*助手\s*(?:请注意|请注意|请)"
    r"|(?:致|给)\s*(?:ai|模型|助手)\s*[:：]"
    r").*$",
    re.IGNORECASE | re.MULTILINE)

_REMOVED = "[instruction removed]"


def strip_instructions(text: str, stats: ShieldStats | None = None) -> str:
    """中和检索文本里的间接提示注入。

    三步, 每步只做一件事, 便于单独断言:
      1. 去掉零宽/双向控制字符(它们本身也可能拆开关键词)
      2. 中和伪造的来源/系统标签(防止伪造上下文边界)
      3. 整行剔除「对 AI 说话」的指令行

    返回处理后的文本; 剔除行数累加进 stats.instructions_removed。
    """
    st = stats if stats is not None else ShieldStats()
    cleaned = _ZERO_WIDTH.sub("", text)

    def _tag_repl(m: re.Match) -> str:
        st.bump("fake_tag")
        return "[tag neutralised]"

    cleaned = _FAKE_TAG.sub(_tag_repl, cleaned)

    def _line_repl(m: re.Match) -> str:
        st.instructions_removed += 1
        return _REMOVED

    return _INSTRUCTION_LINE.sub(_line_repl, cleaned)


# ============================================================
# 组合入口: 进 LLM 前 / 出 LLM 后
# ============================================================


def sanitize_for_llm(text: str, stats: ShieldStats | None = None) -> str:
    """检索文本进 LLM 前的完整处理: 先剔除指令, 再掩码标识。

    顺序不能反: 若先掩码, 注入文本里的关键词可能被标签切碎, 反而绕过指令扫描。
    """
    st = stats if stats is not None else ShieldStats()
    stripped = strip_instructions(text, st)
    return mask_identifiers(stripped, st)


def rescan_answer(answer: str, shown: set[str], user_typed: set[str],
                  stats: ShieldStats | None = None) -> str:
    """答案出 LLM 后的复检: 掩码「模型没被展示过、用户也没自己输入过」的标识。

    这是防幻觉泄露的第二道闸: 模型可能从训练记忆或改写中凭空产生一个卡号。
    即便它碰巧是对的, 用户也不该从系统里拿到它 —— 因为它不来自任何一份
    经过权限校验的文档。

    shown:      送进 LLM 的上下文里出现过的标识(已掩码的标签态, 故不参与放行)
    user_typed: 用户自己在问题里输入的标识(他自己知道的, 系统无需再藏)
    """
    st = stats if stats is not None else ShieldStats()
    return mask_identifiers(answer, st, keep=user_typed)
