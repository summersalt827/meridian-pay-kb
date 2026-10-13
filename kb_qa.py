#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小知识库问答系统 / 参数化 Agent 工作流平台 (Minimal KB-QA + Agent Builder)
============================================================================
特性:
  1. 摄入 Markdown 文档并按标题/段落分块
  2. 关键词检索 (BM25) + 向量检索 (TF-IDF 余弦相似, 可选真实稠密向量)
  3. 混合检索 + LLM 生成回答 (OpenAI 兼容接口, 无 Key 时回退抽取式回答)
  4. 简单 RBAC: admin 看全部, viewer/sales 只能看指定目录
  5. 每次问答写审计日志 (SQLite: 时间/用户/问题/来源文档)
  6. 参数化 Agent 工作流: 规划 -> 多子问题并行检索(各自过 RBAC) -> 合并去重 -> 合成
     同一套多跳骨架 + 不同的 plan/synth prompt = 不同的业务 agent (knowledge_qa / gtm_research)
  7. 来源平台标注 (Confluence / Drive / Jira): 只存在于 DOC_SOURCES 代码常量,
     在摄入时挂到 chunk 上, 绝不写入文档正文, 因此不污染检索词面
  8. retriever 层 min_score 相关性阈值 (11/28 重写打分口径):
     旧口径把 BM25 与 TF-IDF 各自 min-max 归一化后加权, 排第一的分数恒接近 1.0,
     阈值 0.3 几乎从不触发——完全无关的长句也能带著 4 条来源过门 (实测可复现)。
     新口径改为「绝对可比分」relevance = BM25 分 / 该查询的 BM25 自匹配上界:
     以「把查询自身当文档」的 BM25 得分作为该查询的理论上界, 语料里不存在的
     查询词 idf 极高会抬升上界, 从而把「碰巧撞上一个常见字」的文档压到低分。
     分数落在 0~1 且跨查询可比, 因此阈值判定与展示分数第一次真正有意义。

依赖: numpy, requests (均为沙箱预装), 其余全部标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

import numpy as np

# requests 为可选依赖 (仅接入 LLM/稠密向量时需要); 延迟导入以保持离线零额外依赖

# ============================================================
# 来源平台标注 (connector 叙事)
# ============================================================

# 相对 kb_root 的路径 -> 来源平台名。
# ⚠️ 该映射只存在于代码常量里: 文档正文绝不出现平台词, 否则会污染 BM25/TF-IDF 词面。
DEFAULT_DOC_SOURCE = "Confluence"          # 未登记路径的默认平台

DOC_SOURCES: dict[str, str] = {           # 键统一用 "目录/文件名.md" 的相对路径, 避免重名
    "public/faq.md": "Confluence",
    "public/getting-started.md": "Confluence",
    "internal/roadmap.md": "Confluence",
    "internal/ops.md": "Confluence",
    "gtm/sales-playbook.md": "Drive",
    "gtm/pricing.md": "Drive",
    "gtm/case-studies.md": "Drive",
    "gtm/icp-targeting.md": "Jira",
}


def doc_source_for(rel_path: str) -> str:
    """按相对路径查来源平台名; 未登记则回落到默认平台。"""
    norm = str(rel_path).replace("\\", "/").lstrip("./")
    return DOC_SOURCES.get(norm, DEFAULT_DOC_SOURCE)


# 无命中时的统一话术 (单跳与多跳共用)
NO_CONTEXT_ANSWER = "未检索到相关文档, 无法回答。"

# ============================================================
# 数据结构
# ============================================================

@dataclass
class Chunk:
    """一个文档分块。"""
    doc_id: str          # 文档唯一 id
    path: str            # 相对 kb_root 的路径 (用于 RBAC 过滤)
    title: str           # 所属标题链, 如 "指南 / 安装"
    content: str         # 分块正文
    meta: dict = field(default_factory=dict)
    doc_source: str = DEFAULT_DOC_SOURCE   # 来源平台名 (仅用于展示, 不参与检索)

    def fingerprint(self) -> str:
        return hashlib.md5(self.content.encode("utf-8")).hexdigest()[:12]


@dataclass
class User:
    """用户与权限。"""
    username: str
    role: str                              # 'admin' | 'sales' | 'viewer'
    allowed_dirs: tuple[str, ...] = ()      # 受限角色可访问的目录 (相对 kb_root)

    def can_access(self, rel_path: str) -> bool:
        """RBAC 判定: admin 全通过; 其余需路径位于 allowed_dirs 之下。"""
        if self.role == "admin":
            return True
        # 归一化路径, 防止 ../ 逃逸
        norm = os.path.normpath(rel_path).replace("\\", "/").lstrip("./")
        for d in self.allowed_dirs:
            d = os.path.normpath(d).replace("\\", "/").lstrip("./")
            if norm == d or norm.startswith(d.rstrip("/") + "/"):
                return True
        return False


@dataclass
class Answer:
    """问答结果。"""
    question: str
    answer: str
    sources: list[dict]    # [{path, title, score, source(平台)}]
    mode: str              # 'llm' | 'extractive' | 'no_context'
    user: str
    ts: str
    sub_queries: list[str] = field(default_factory=list)  # agent 拆解出的子问题 (单跳时为 [原问题])


def _source_dict(chunk: Chunk, score: float) -> dict:
    """统一构造来源字典: 新增 source(平台) 字段, 缺省回落默认平台, 向后兼容。"""
    return {
        "path": chunk.path,
        "title": chunk.title,
        "score": round(float(score), 4),
        "source": getattr(chunk, "doc_source", DEFAULT_DOC_SOURCE),
    }

# ============================================================
# 配置
# ============================================================

@dataclass
class Config:
    kb_root: str = "sample_docs"            # Markdown 根目录
    db_path: str = "kb_audit.db"            # 审计日志 SQLite 路径
    chunk_size: int = 800                   # 分块目标字符数
    chunk_overlap: int = 120                # 分块重叠
    top_k: int = 4                          # 返回来源数
    alpha: float = 0.5                      # 混合权重 (向量占比), 0=纯关键词, 1=纯向量
    # 相关性阈值 (relevance 口径, 0~1 绝对可比): 低于此分视为噪声, 直接丢弃;
    # RBAC 过滤后若无任何来源过门则判 no_context, 杜绝低分文档凑数。
    # 0.17 由 27 组标注样本实测标定, 三类恰好完全分离:
    #   语料有答案的提问  最低 0.189 (「折扣规则是什么」→ 命中定价折扣章节)
    #   语料只沾一两句的  最高 0.152 (「打印机坏了找谁修」→ 判无命中可接受)
    #   语料完全无关的    最高 0.145 (「今天天气怎么样」)
    # 取 0.152 与 0.189 的几何中点。改这个值前请重跑 test_relevance.py。
    min_score: float = 0.17
    candidate_pool: int = 20                # 混合召回候选池下限 (相关性重排的输入窗口)
    # agent 规划模式: auto=LLM 优先+离线兜底 / llm=仅 LLM(不可用则单跳) / offline=仅离线规划
    plan_mode: str = "auto"
    # LLM (OpenAI 兼容)
    llm_base_url: str = ""                 # 为空则走抽取式回退
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 30
    # 稠密向量 (可选), 不配置则用 TF-IDF
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    use_dense_embeddings: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            kb_root=os.getenv("KB_ROOT", "sample_docs"),
            db_path=os.getenv("KB_DB", "kb_audit.db"),
            min_score=float(os.getenv("KB_MIN_SCORE", "0.17")),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            embed_base_url=os.getenv("EMBED_BASE_URL", ""),
            embed_api_key=os.getenv("EMBED_API_KEY", ""),
            embed_model=os.getenv("EMBED_MODEL", "text-embedding-3-small"),
            use_dense_embeddings=os.getenv("USE_DENSE_EMBED", "0") == "1",
            plan_mode=(os.getenv("KB_PLAN_MODE", "auto") or "auto").strip().lower(),
        )

# ============================================================
# 参数化 Agent 工作流注册表
# ============================================================

@dataclass
class AgentWorkflow:
    """一个业务 agent 的参数化定义: 同一套多跳骨架 + 不同的规划/合成 prompt。"""
    id: str                                     # 工作流 id (接口传参用)
    name: str                                   # 对外展示名
    plan_prompt: str                            # 规划步 prompt: 拆子问题
    synth_prompt: str                           # 合成步 prompt: 生成带引用答案
    max_sub_questions: int = 4                  # 子问题上限
    sub_queries_authoritative: bool = False     # True: 子问题即权威检索语句, 深挖结果按原分合并
                                                # (不要求「原问题已召回该文档」); 用于有固定维度骨架
                                                # 的工作流 (如 gtm_research), 客户调研问题本身不含
                                                # 维度词, 只有子问题才含「套餐/折扣」等检索词
    # 以下为「LLM 不可用」时的确定性兜底参数 (保证离线也能跑通多跳与结构化产出)
    offline_aspects: tuple[str, ...] = ()       # 离线规划骨架 (确定性维度 / 方面词)
    aspect_with_core: bool = False              # True: 方面词需拼上问题核心才成为检索语句
    report_sections: tuple[tuple[str, str], ...] = ()  # 离线简报章节: (标题, 该章节检索词)
    human_decisions: tuple[str, ...] = ()       # 「需人工决策」清单模板


DEFAULT_WORKFLOW_ID = "knowledge_qa"

WORKFLOWS: dict[str, AgentWorkflow] = {
    "knowledge_qa": AgentWorkflow(
        id="knowledge_qa",
        name="知识问答",
        plan_prompt=(
            "你是检索规划器。把用户问题拆成若干个可以各自独立检索知识库的子问题。\n"
            "要求: 子问题之间不重复; 每个子问题都能单独拿去检索; 合起来覆盖原问题的全部意图;\n"
            "不要改写专业术语, 保留原问题里的关键词。\n"
            "⚠️ 每个子问题只包含本主题的关键词, 严禁把其他子问题的关键词带进来:\n"
            "例如「薪酬带宽是多少，打折规则是什么」必须拆成「薪酬带宽是多少」与「打折规则是什么」两个子问题,\n"
            "打折子问题里不得再出现薪酬。\n"
            "只输出 JSON 字符串数组, 不要解释、不要代码块标记, 例如: [\"子问题1\", \"子问题2\"]。"
        ),
        synth_prompt=(
            "你是知识库问答助手。只根据下方来源资料回答用户问题, 不得编造资料之外的信息;\n"
            "资料里没有的部分直接说明「资料未覆盖」, 不要猜测。\n"
            "回答分点、简洁, 并在每个结论后用括号标注引用的来源编号, 例如 (来源1, 来源3)。"
        ),
        max_sub_questions=4,
        # 通用问答没有固定维度, 离线时走「探测 -> 命中标题深挖」的数据驱动路径
        offline_aspects=(),
    ),
    "gtm_research": AgentWorkflow(
        id="gtm_research",
        name="账户情报",
        plan_prompt=(
            "你是 GTM 调研规划器。按以下四个维度把客户调研请求拆成子问题, 每个维度最多 1 个子问题:\n"
            "1) 信号层判断: 融资、招聘、新市场/新主体等优先级信号;\n"
            "2) ICP 分层与路由: 客户分层画像与线索路由规则;\n"
            "3) 套餐与折扣: 推荐套餐、功能边界与折扣审批权限;\n"
            "4) 客户案例与合规边界: 同行业案例、异议处理话术与合规/尽调红线。\n"
            "每个子问题都要能单独拿去检索内部文档, 不要带上客户的完整背景描述。\n"
            "只输出 JSON 字符串数组, 不要解释、不要代码块标记。"
        ),
        synth_prompt=(
            "你是 GTM 账户情报助手。只依据下方来源资料与用户提供的公司事实, 输出一份「账户情报简报」, 结构固定为:\n"
            "## 一、公司概况与信号评分卡\n"
            "- 公司概况一句话; 然后 Fit×Timing×Access×Intent 四维各 0-25 分、总分 100, 映射 tier:\n"
            "  80-100 Hot(AE 2 小时内响应) / 60-79 Warm(SDR 24 小时内首触) / 40-59 Cold(培育序列或 PARK) / <40 不追\n"
            "- 每个得分必须标注依据: 「用户提供的事实」或「文档规则(来源编号)」; 用户未提供的事实维度写「资料未覆盖, 需人工补充」且该维记 0\n"
            "- 命中「不追清单」红线时直接输出 tier=不追并说明命中的红线条款(来源编号)\n"
            "## 二、ICP 分层判断与路由建议\n"
            "- tier 驱动路由(自助/SDR/AE), 与评分卡联动; 命中红线时输出「不追 / PARK」而非硬推\n"
            "## 三、推荐套餐与折扣权限说明\n"
            "## 四、客户案例与合规边界\n"
            "- 附 1 条该客户类型最可能遇到的异议及应对话术 (素材取自来源资料中的异议处理表, 标注来源编号)\n"
            "## 五、首触邮件草稿\n"
            "- 主题行(≤8 个英文词) + 正文 3-4 句(钩子=评分卡里最强的 1 个信号 + 一句价值主张 + 一个行动邀请) + 合规注记一行\n"
            "- 禁用 AI 味词: delve, leverage, elevate, unlock, seamless, cutting-edge, \"I hope this email finds you well\", \"Let's schedule a call\", 赋能, 一站式, 无缝, 极致, 闭环, 共创, 对齐(动词)\n"
            "- 合规注记格式: 「合规注记: 基于公开融资/招聘信息首触(PDPA 允许); 含退订机制(CAN-SPAM); 邮件为草稿, 发送前需销售经理批准」\n"
            "- 邮件素材只引用来源资料中的话术/案例(标注来源编号), 不编造客户信息\n"
            "## 六、预计节省工时\n"
            "- 依据客户规模与信号强度, 估算本简报替代人工调研每周可节省的工时 (如 2-3 小时 → 30 秒; 标注为 LLM 估算)\n"
            "要求: 每小节末尾用括号标注引用的来源编号, 例如 (来源2); 资料未覆盖的内容写「资料未覆盖, 需人工补充」, 不要编造客户信息。\n"
            "每个结论标注依据类型: 文档规则(来源X) / 用户提供事实 / LLM 推断。\n"
            "简报最后附一个「## 需人工决策」小节, 列出必须由人拍板的事项, 例如折扣是否特批、是否触发合规评估、\n"
            "是否需要 CFO 双签、首触邮件是否发送(默认草稿)——agent 完成分析, 决策权留给人。"
        ),
        max_sub_questions=4,
        sub_queries_authoritative=True,
        offline_aspects=(
            "客户分层画像与线索路由规则",
            "套餐定价与折扣审批权限",
            "同行业客户案例与成交话术",
            "销售 SOP 与赢单丢单复盘",
        ),
        report_sections=(
            ("公司概况与信号评分卡", "评分 信号 融资 招聘 扩张 新市场 行业 优先级 红线 不追"),
            ("ICP 分层判断与路由建议", "分层 画像 路由 自助 SDR AE 首触 打法"),
            ("推荐套餐与折扣权限说明", "套餐 月费 费率 折扣 审批 年付 优惠 报价单"),
            ("客户案例与合规边界", "案例 行业 合规 尽调 风控 制裁 KYC"),
            ("首触邮件草稿", "邮件 话术 首触 主题行 合规"),
            ("预计节省工时", "工时 节省 效率 小时 人工"),
        ),
        human_decisions=(
            "折扣超过销售总监 15% 权限时, 是否提交 CEO 特批？",
            "是否需要 CFO 双签 (超 30% 折扣或特殊账期)？",
            "客户行业 / 股东结构是否触发合规评估 (评估期间不得报价)？",
            "是否为本单配置专属客户经理与 SLA (仅 Enterprise)？",
            "路由结果是否需销售运营申诉 (不允许人工抢占路由)？",
            "首触邮件是否发送 (默认草稿, 销售经理批准后进入发送队列)？",
        ),
    ),
}

# ============================================================
# 分词器 (中英文混合)
# ============================================================

_WORD_RE = re.compile(r"[a-z0-9]+|[一-鿿]", re.UNICODE)

def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"

def tokenize(text: str) -> list[str]:
    """中英文混合分词: 英文按词, 中文按单字 + 相邻二字 bigram。"""
    text = text.lower()
    raw = _WORD_RE.findall(text)
    tokens = list(raw)
    for i in range(len(raw) - 1):
        a, b = raw[i], raw[i + 1]
        if len(a) == 1 and _is_cjk(a) and len(b) == 1 and _is_cjk(b):
            tokens.append(a + b)
    return tokens

# ============================================================
# Markdown 摄入与分块
# ============================================================

class MarkdownIngestor:
    """读取目录下所有 .md 文件并分块。"""

    def __init__(self, kb_root: str, chunk_size: int = 800, overlap: int = 120):
        self.kb_root = os.path.abspath(kb_root)
        self.chunk_size = chunk_size
        self.overlap = overlap

    def ingest(self) -> list[Chunk]:
        chunks: list[Chunk] = []
        root = Path(self.kb_root)
        if not root.exists():
            return chunks
        for md in sorted(root.rglob("*.md")):
            rel = str(md.relative_to(root)).replace("\\", "/")
            try:
                text = md.read_text(encoding="utf-8")
            except Exception:
                continue
            chunks.extend(self._chunk(text, rel))
        return chunks

    def _chunk(self, text: str, rel_path: str) -> list[Chunk]:
        sections = self._split_by_headers(text)
        doc_id = hashlib.md5(rel_path.encode()).hexdigest()[:10]
        doc_source = doc_source_for(rel_path)     # 平台名来自代码常量, 不读文档正文
        out: list[Chunk] = []
        for header_path, body in sections:
            title = " / ".join(header_path) if header_path else rel_path
            header_ctx = " / ".join(header_path)
            for piece in self._split_text(body, self.chunk_size, self.overlap):
                piece = piece.strip()
                if not piece:
                    continue
                content = (header_ctx + "\n" + piece) if header_ctx else piece
                out.append(Chunk(
                    doc_id=doc_id, path=rel_path, title=title,
                    content=content, meta={"header": header_ctx},
                    doc_source=doc_source,
                ))
        return out

    @staticmethod
    def _split_by_headers(text: str) -> list[tuple[list[str], str]]:
        """按 Markdown 标题切分, 返回 [(标题链, 该节正文)]。"""
        sections: list[tuple[list[str], str]] = []
        stack: list[str] = []
        buf: list[str] = []
        for line in text.split("\n"):
            m = re.match(r"^(#{1,6})\s*(.*)$", line)
            if m:
                # flush
                sections.append((list(stack), "\n".join(buf)))
                buf = []
                level = len(m.group(1))
                title = m.group(2).strip()
                stack = stack[: level - 1]
                # 替换/追加到当前层
                if len(stack) >= level:
                    stack[level - 1] = title
                else:
                    while len(stack) < level - 1:
                        stack.append("")
                    stack.append(title)
                buf.append(line)  # 标题行也保留在正文里
            else:
                buf.append(line)
        sections.append((list(stack), "\n".join(buf)))
        # 去掉空节
        return [(h, b) for h, b in sections if b.strip()]

    @staticmethod
    def _split_text(content: str, chunk_size: int, overlap: int) -> list[str]:
        """按段落打包, 超长段落硬切分。"""
        paragraphs = re.split(r"\n\s*\n", content)
        chunks: list[str] = []
        buf = ""
        for p in paragraphs:
            if len(buf) + len(p) + 2 <= chunk_size:
                buf = (buf + "\n\n" + p) if buf else p
            else:
                if buf:
                    chunks.append(buf)
                if len(p) > chunk_size:
                    step = max(1, chunk_size - overlap)
                    for i in range(0, len(p), step):
                        chunks.append(p[i : i + chunk_size])
                else:
                    buf = p
        if buf:
            chunks.append(buf)
        return chunks

# ============================================================
# 向量化后端 (TF-IDF 默认, 可选稠密)
# ============================================================

class TfidfEmbedder:
    """稀疏 TF-IDF 向量空间模型 (离线, 零额外依赖)。"""

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None
        self.matrix: np.ndarray | None = None  # (n_chunks, vocab_size), L2 归一化
        self.dim = 0

    def fit(self, token_lists: list[list[str]]):
        n = len(token_lists)
        # 词表与文档频率
        df: dict[str, int] = {}
        for toks in token_lists:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.vocab = {t: i for i, t in enumerate(sorted(df))}
        V = len(self.vocab)
        self.dim = V
        # smooth idf: log((1+n)/(1+df)) + 1
        idf = np.zeros(V, dtype=np.float32)
        for t, i in self.vocab.items():
            idf[i] = float(np.log((1 + n) / (1 + df[t])) + 1.0)
        self.idf = idf
        # 构建 TF-IDF 矩阵并 L2 归一化
        mat = np.zeros((n, V), dtype=np.float32)
        for r, toks in enumerate(token_lists):
            for t in toks:
                mat[r, self.vocab[t]] += 1.0
        mat *= idf  # 广播
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = mat / norms
        return self

    def embed_query(self, tokens: list[str]) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for t in tokens:
            if t in self.vocab:
                v[self.vocab[t]] += 1.0
        v *= self.idf
        n = float(np.linalg.norm(v))
        if n > 0:
            v /= n
        return v

    def search(self, query_tokens: list[str], top_k: int) -> list[tuple[int, float]]:
        if self.matrix is None or self.dim == 0:
            return []
        q = self.embed_query(query_tokens)
        scores = self.matrix @ q  # 余弦 (已归一化)
        idx = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]


class DenseEmbedder:
    """可选: 调用 OpenAI 兼容 embeddings 接口获取稠密向量。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.matrix: np.ndarray | None = None
        self.dim = 0

    def _embed(self, texts: list[str]) -> np.ndarray:
        import requests  # 延迟导入
        url = self.base_url + "/embeddings"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        vecs = [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]
        return np.array(vecs, dtype=np.float32)

    def fit(self, texts: list[str]):
        if not texts:
            self.matrix = np.zeros((0, 0), dtype=np.float32)
            return self
        # 分批避免超长
        batch = 64
        parts = [self._embed(texts[i : i + batch]) for i in range(0, len(texts), batch)]
        self.matrix = np.vstack(parts)
        self.dim = self.matrix.shape[1]
        norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix /= norms
        return self

    def embed_query(self, text: str) -> np.ndarray:
        v = self._embed([text])[0]
        n = float(np.linalg.norm(v))
        if n > 0:
            v /= n
        return v

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        if self.matrix is None or self.matrix.shape[0] == 0:
            return []
        q = self.embed_query(query)
        scores = self.matrix @ q
        idx = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]

# ============================================================
# BM25 关键词检索
# ============================================================

class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_lens: list[int] = []
        self.avgdl: float = 0.0
        self.tf: list[dict[str, int]] = []
        self.df: dict[str, int] = {}
        self.n: int = 0

    def fit(self, token_lists: list[list[str]]):
        self.tf = []
        self.df = {}
        self.doc_lens = []
        for toks in token_lists:
            self.doc_lens.append(len(toks))
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self.tf.append(tf)
            for t in tf:
                self.df[t] = self.df.get(t, 0) + 1
        self.n = len(token_lists)
        self.avgdl = (sum(self.doc_lens) / self.n) if self.n else 0.0
        return self

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return float(np.log((self.n - df + 0.5) / (df + 0.5) + 1.0))

    def idf_of(self, term: str) -> float:
        """公开 idf 查询 (相关性打分需要按 idf 加权统计查询覆盖率)。"""
        return self._idf(term)

    def self_ceiling(self, query_tokens: list[str]) -> float:
        """把「查询自身」当作一篇文档算 BM25, 作为该查询的得分理论上界。

        关键性质: 语料里根本不存在的查询词 df=0, idf 极高, 会把上界抬得很高,
        于是「靠一个常见字撞上文档」的绝对分虽然可能不低, 归一化后依然很低。
        这正是区分「真的相关」与「碰巧撞字」的信号, 也是旧 min-max 归一化
        口径完全缺失的部分 (它只看同一次查询内部的相对高低, 跨查询不可比)。
        """
        if not query_tokens:
            return 0.0
        dl = float(len(query_tokens))
        total = 0.0
        for t in set(query_tokens):
            denom = 1.0 + self.k1 * (1.0 - self.b + self.b * dl / (self.avgdl or 1.0))
            total += self._idf(t) * (self.k1 + 1.0) / denom
        return float(total)

    def score(self, query_tokens: list[str]) -> np.ndarray:
        scores = np.zeros(self.n, dtype=np.float32)
        for q in set(query_tokens):
            idf = self._idf(q)
            for i in range(self.n):
                f = self.tf[i].get(q, 0)
                if f == 0:
                    continue
                dl = self.doc_lens[i]
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores

    def search(self, query_tokens: list[str], top_k: int) -> list[tuple[int, float]]:
        if self.n == 0:
            return []
        s = self.score(query_tokens)
        idx = np.argsort(-s)[:top_k]
        return [(int(i), float(s[i])) for i in idx if s[i] > 0]

# ============================================================
# 检索器 (混合 + RBAC)
# ============================================================

def _minmax(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-9:
        return np.zeros_like(arr)
    return (arr - lo) / (hi - lo)


class Retriever:
    """两段式检索: 混合分召回候选 → 绝对相关度重排与过门 → RBAC 过滤。

    为什么要分两段:
      第一段 hybrid = alpha×向量 + (1-alpha)×BM25, 各自 min-max 归一化后加权。
      它擅长「本次查询内部谁最靠前」, 但归一化后第一名恒接近 1.0, 分数跨查询
      不可比, 因此**不能**拿来做「有没有命中」的判定——旧实现正是把阈值设在这
      个量上, 导致阈值形同虚设。

      第二段 relevance = BM25 分 / 该查询的 BM25 自匹配上界, 落在 0~1 且跨查询
      可比, 既用于最终排序, 也用于 min_score 过门。返回的分数就是这个 relevance,
      于是「排序 / 门 / 展示」三者第一次语义一致。

    RBAC 与旧实现一样在候选阶段逐条生效: 越权分块根本不会进入结果。
    """

    def __init__(self, chunks: list[Chunk], embedder, bm25: BM25Index,
                 alpha: float = 0.5, top_k: int = 4, min_score: float = 0.17,
                 candidate_pool: int = 20):
        self.chunks = chunks
        self.embedder = embedder
        self.bm25 = bm25
        self.alpha = alpha
        self.top_k = top_k
        self.min_score = min_score
        self.candidate_pool = candidate_pool

    # ---- 相关性打分 (绝对可比分, 0~1) ----

    def relevance(self, q_tokens: list[str], idx: int,
                  ceiling: float | None = None, bm25_raw: float | None = None) -> float:
        """单个分块相对查询的绝对相关度 = BM25 分 / 该查询的 BM25 自匹配上界。

        曾经尝试再叠加一项「查询信息量覆盖率」(命中的查询实词 idf 之和 / 全部
        查询实词 idf 之和), 实测后放弃: 中文分词产生大量无意义 bigram
        (如「折扣能批到几折」里的 能批/批到/到几/几折), 这些词在语料里 df=0,
        idf 极高, 会把覆盖率的分母撑大而分子几乎不变, 于是**越精炼的提问得分
        越低**——「折扣能批到几折」因此被误杀 (0.165), 反而不如啰嗦的
        「大客户报价折扣能批到几折？」(0.377)。单用自匹配归一化则稳定在 0.23。
        """
        if ceiling is None:
            ceiling = self.bm25.self_ceiling(q_tokens)
        if ceiling <= 0:
            return 0.0
        raw = bm25_raw if bm25_raw is not None else float(self.bm25.score(q_tokens)[idx])
        return max(0.0, min(1.0, raw / ceiling))

    # ---- 检索 ----

    def search(self, query: str, user: User, top_k: int | None = None) -> list[tuple[Chunk, float]]:
        k = top_k or self.top_k
        if not self.chunks:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        ceiling = self.bm25.self_ceiling(q_tokens)
        if ceiling <= 0:                      # 查询里没有任何可检索词
            return []

        # ---- 第一段: 混合分召回候选 ----
        n = len(self.chunks)
        v_scores = np.zeros(n, dtype=np.float32)
        for idx, sc in self.embedder.search(q_tokens, n):
            v_scores[idx] = sc
        k_scores = np.zeros(n, dtype=np.float32)
        for idx, sc in self.bm25.search(q_tokens, n):
            k_scores[idx] = sc

        v_n, k_n = _minmax(v_scores), _minmax(k_scores)
        hybrid = self.alpha * v_n + (1 - self.alpha) * k_n

        # 候选窗口取 max(k*4, candidate_pool): 给相关性重排留出足够输入,
        # 避免真正相关的分块被 hybrid 的相对排序挡在窗口外。
        pool = max(k * 4, self.candidate_pool)
        order = np.argsort(-hybrid)[:pool]

        # ---- 第二段: RBAC 过滤 + 绝对相关度过门 + 重排 ----
        scored: list[tuple[Chunk, float]] = []
        for i in order:
            if hybrid[i] <= 0:
                continue
            c = self.chunks[i]
            if not user.can_access(c.path):     # 目录级权限在这里生效
                continue
            rel = self.relevance(q_tokens, int(i), ceiling=ceiling, bm25_raw=float(k_scores[i]))
            if rel < self.min_score:            # 低于阈值一律丢弃, 不凑数
                continue
            scored.append((c, rel))
        scored.sort(key=lambda p: -p[1])
        return scored[:k]

# ============================================================
# LLM 生成 (OpenAI 兼容, 带抽取式回退)
# ============================================================

class Generator:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---- 能力探测 ----

    def llm_ready(self) -> bool:
        """是否配置了可用的 LLM (agent 据此决定规划/合成走 LLM 还是离线兜底)。"""
        return bool(self.cfg.llm_base_url and self.cfg.llm_api_key)

    # ---- 单跳 ----

    def answer(self, query: str, sources: list[tuple[Chunk, float]]) -> tuple[str, str]:
        """返回 (answer_text, mode)。"""
        ctx = self.build_context(sources)
        if not sources or not ctx.strip():
            return NO_CONTEXT_ANSWER, "no_context"

        if self.llm_ready():
            try:
                return self._call_llm(query, ctx), "llm"
            except Exception as e:
                # 降级到抽取式
                return self._extractive(query, sources) + f"\n(LLM 不可用: {e})", "extractive"
        return self._extractive(query, sources), "extractive"

    # ---- 多跳: 规划 / 合成 ----

    def plan(self, question: str, workflow: AgentWorkflow) -> str:
        """规划步: 要求 LLM 返回子问题 JSON 数组 (原文返回, 解析交给调用方)。"""
        prompt = (
            f"{workflow.plan_prompt}\n\n最多 {workflow.max_sub_questions} 个子问题。\n\n"
            f"用户问题: {question}"
        )
        return self._post_chat(
            "你是一个检索规划器, 只输出 JSON 数组, 不输出任何解释。", prompt, temperature=0.1,
        )

    def synthesize(self, question: str, sources: list[tuple[Chunk, float]],
                   synth_prompt: str) -> str:
        """合成步: 用工作流的 synth_prompt 生成带来源编号的最终答案。"""
        ctx = self.build_context(sources)
        prompt = f"{synth_prompt}\n\n来源资料:\n{ctx}\n\n用户问题 / 调研请求: {question}"
        return self._post_chat("你是一个严谨的企业知识助手。", prompt, timeout=60)

    # ---- 底层 ----

    @staticmethod
    def build_context(sources: list[tuple[Chunk, float]], max_chars: int = 900) -> str:
        """把来源拼成带编号的上下文 (平台名仅在此处展示, 不进索引); 单来源截断防撑爆上下文。"""
        parts: list[str] = []
        for i, (c, _) in enumerate(sources):
            content = c.content.strip().replace("\n", " ")
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            parts.append(
                f"[来源{i+1}] {c.path} :: {c.title}（平台: {getattr(c, 'doc_source', DEFAULT_DOC_SOURCE)}）\n{content}"
            )
        return "\n\n".join(parts)

    def _post_chat(self, system: str, prompt: str, temperature: float = 0.2,
                   timeout: int | None = None, retries: int = 1) -> str:
        """OpenAI 兼容 chat/completions 的通用调用 (瞬时故障自动重试一次, 保演示稳定)。"""
        import requests  # 延迟导入
        if not self.llm_ready():
            raise RuntimeError("未配置 LLM_BASE_URL / LLM_API_KEY")
        url = self.cfg.llm_base_url.rstrip("/") + "/chat/completions"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.cfg.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.cfg.llm_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                    },
                    timeout=timeout or self.cfg.llm_timeout,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5)     # 瞬时故障: 重试一次 (视频录制稳定性)
        assert last_err is not None
        raise last_err

    def _call_llm(self, query: str, ctx: str) -> str:
        prompt = (
            "你是知识库问答助手。只根据下方来源资料回答用户问题; "
            "若资料中没有答案, 直接说明无法回答, 不要编造。"
            "回答末尾用括号标注引用的来源编号, 例如 (来源1, 来源3)。\n\n"
            f"来源资料:\n{ctx}\n\n问题: {query}"
        )
        return self._post_chat("你是一个严谨的知识库问答助手。", prompt)

    @staticmethod
    def _extractive(query: str, sources: list[tuple[Chunk, float]]) -> str:
        """无 LLM 时: 返回最相关分块摘要 + 引用。"""
        q_tokens = set(tokenize(query))
        best, best_score = None, -1.0
        for c, s in sources:
            overlap = len(q_tokens & set(tokenize(c.content)))
            score = s + overlap * 0.1
            if score > best_score:
                best, best_score = c, score
        if best is None:
            return "未找到相关内容。"
        snippet = best.content.strip().replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        return f"[抽取式回答] {snippet}\n(来源: {best.path} :: {best.title})"

# ============================================================
# 子问题规划 (LLM 规划 + 确定性离线兜底)
# ============================================================

# 并列结构的切分符: 用于「A 是多少, B 是什么」这类多意图问题
_Q_SPLIT_RE = re.compile(r"[，,；;]|以及|并且|还有|同时|另外")

# 检索侧的同义改写 (仅作用于 agent 子问题的查询语句, 不写入任何文档正文):
# 业务口语词 -> 文档用词, 避免「打折」这类口语词在语料里零命中
QUERY_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("打折", "折扣"),
    ("折扣价", "折扣"),
    ("报价单", "报价单"),
)


def _rewrite_query(query: str) -> str:
    """对子问题做口语词 -> 文档用词的同义改写 (只改查询侧, 不动语料)。"""
    out = query or ""
    for src, dst in QUERY_SYNONYMS:
        if src != dst:
            out = out.replace(src, dst)
    return out


def _parse_json_array(raw: str) -> list[str]:
    """从 LLM 输出里提取字符串数组; 兼容 ```json 代码块包裹与 dict 形式。"""
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        obj = json.loads(text[start : end + 1])
    except Exception:
        return []
    if not isinstance(obj, list):
        return []
    out: list[str] = []
    for x in obj:
        if isinstance(x, str):
            s = x.strip()
        elif isinstance(x, dict):
            s = str(x.get("question") or x.get("sub_question") or x.get("q") or "").strip()
        else:
            s = ""
        if s and s not in out:
            out.append(s)
    return out


def _split_question(question: str) -> list[str]:
    """按并列结构切分多意图问题; 返回 >=2 条才视为可拆。"""
    parts = [p.strip(" ，,；;。?？") for p in _Q_SPLIT_RE.split(question or "")]
    parts = [p for p in parts if len(p) >= 4]
    out: list[str] = []
    for p in parts:
        if p not in out:
            out.append(p)
    return out


def _core_of(question: str, limit: int = 14) -> str:
    """取问题的核心片段 (去掉尾部标点并截断), 用于与维度词拼成子问题。"""
    q = (question or "").strip().rstrip("？?。. ")
    return q[:limit]


def _aspects_from_hits(hits: list[tuple[Chunk, float]], limit: int) -> list[str]:
    """从探测命中的分块标题里抽「方面词」, 作为深挖子问题 (数据驱动的多跳)。

    每个文档最多贡献 1 个方面词, 尽量让第二跳覆盖到不同文档。
    """
    aspects: list[str] = []
    seen: set[str] = set()
    used_docs: set[str] = set()
    for c, _s in hits:
        header = str(c.meta.get("header") or c.title or "")
        segs = [x.strip() for x in header.split("/") if x.strip()]
        if len(segs) < 2:                         # 只取二级及以下的章节标题, 跳过文档级标题
            continue
        cand = segs[-1]
        cand = re.sub(r"^[一二三四五六七八九十]+、\s*", "", cand).strip()
        # 跳过带冒号/括号的具体条目名与过长短语, 只保留章节级方面词
        if ("：" in cand or "(" in cand or "（" in cand) or not (3 <= len(cand) <= 12):
            continue
        if cand in seen or c.path in used_docs:
            continue
        seen.add(cand)
        used_docs.add(c.path)
        aspects.append(cand)
        if len(aspects) >= limit:
            break
    return aspects


# 覆盖率统计时忽略的虚词/疑问字, 避免「是什么 / 多少 / 哪里」拉低覆盖度
_STOP_CHARS: set[str] = set(
    "是怎么多少哪那里怎样如何为何吗呢吧请帮我做给并及有没有在对从到要会能可就都也还个些这那您贵上下了的与或吧啊"
)


def _needs_multi_hop(question: str, seeds: list[tuple[Chunk, float]],
                     threshold: float = 0.75) -> bool:
    """判断是否值得多跳: 问题的实词已被首条命中充分覆盖时保持单跳。

    threshold 为首条命中对问题实词的覆盖率下限, 高于该值视为「问得够准, 不必拆」。
    """
    if not seeds:
        return False
    chars = [c for c in (question or "") if _is_cjk(c) and c not in _STOP_CHARS]
    if not chars:
        return False
    top = seeds[0][0]
    hay = set(top.content + " " + top.title)
    cover = sum(1 for c in set(chars) if c in hay) / len(set(chars))
    return cover < threshold


def _dedup_strs(items: Iterable[str], limit: int) -> list[str]:
    """去重并截断到 limit 条。"""
    out: list[str] = []
    for s in items or []:
        s = str(s).strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out

# ============================================================
# 离线结构化简报 (LLM 不可用时的确定性产出)
# ============================================================

# 分层/路由阈值: 与 gtm/icp-targeting.md 保持一致
TIER_ENTERPRISE_HEADCOUNT = 300      # >= 300 人 -> 企业客户层
TIER_MID_HEADCOUNT = 50              # 50-300 人 -> 中端市场
ROUTE_AE_MONTHLY_TXN = 200           # 月跨境付款 > 200 笔 -> AE 直跟
ROUTE_SDR_MONTHLY_TXN = 50           # 50-200 笔 -> SDR 跟进


def _parse_account_signals(question: str) -> list[str]:
    """从调研请求里抽取客户信号 (确定性正则, 不依赖 LLM)。"""
    q = question or ""
    out: list[str] = []

    m_head = re.search(r"(\d+)\s*人", q)
    headcount = int(m_head.group(1)) if m_head else None
    m_txn = re.search(r"(\d+)\s*笔", q)
    txn = int(m_txn.group(1)) if m_txn else None
    m_round = re.search(r"(种子|天使|A|B|C|D|E)\s*轮", q)
    funding = m_round.group(1) + " 轮" if m_round else ""
    m_region = re.search(r"(东南亚|新加坡|马来西亚|印尼|越南|泰国|菲律宾|出海|海外)", q)
    region = m_region.group(1) if m_region else ""

    if headcount is not None:
        if headcount >= TIER_ENTERPRISE_HEADCOUNT:
            tier = f"企业客户层（≥{TIER_ENTERPRISE_HEADCOUNT} 人）"
        elif headcount >= TIER_MID_HEADCOUNT:
            tier = f"中端市场（{TIER_MID_HEADCOUNT}-{TIER_ENTERPRISE_HEADCOUNT} 人）"
        else:
            tier = f"SMB（<{TIER_MID_HEADCOUNT} 人）"
        out.append(f"规模信号：约 {headcount} 人 → {tier}")
    else:
        out.append("规模信号：问题未给出人数，需人工补充")

    if txn is not None:
        if txn > ROUTE_AE_MONTHLY_TXN:
            route = f"AE 直跟（月跨境付款 > {ROUTE_AE_MONTHLY_TXN} 笔，24 小时内首触）"
        elif txn >= ROUTE_SDR_MONTHLY_TXN:
            route = f"SDR 跟进（{ROUTE_SDR_MONTHLY_TXN}-{ROUTE_AE_MONTHLY_TXN} 笔，初筛后转 AE）"
        else:
            route = f"自助通道（< {ROUTE_SDR_MONTHLY_TXN} 笔）"
        out.append(f"用量信号：月均 {txn} 笔跨境付款 → {route}")
    else:
        out.append("用量信号：问题未给出月付款笔数，需人工补充")

    if funding:
        out.append(f"资本信号：{funding} 融资 → 扩张期高优先级线索（预算与决策速度通常更好）")
    else:
        out.append("资本信号：问题未提及融资轮次，需人工补充")

    if region:
        out.append(f"扩张信号：{region} → 新市场 / 新主体，命中优先级信号（加分项）")
    else:
        out.append("扩张信号：问题未提及新市场，需人工补充")

    return out


def _snippet(text: str, limit: int = 180) -> str:
    """单行化并截断文本, 用于简报里的依据摘录。"""
    s = " ".join((text or "").split())
    return s[:limit] + "…" if len(s) > limit else s


def build_offline_report(question: str, sources: list[tuple[Chunk, float]],
                         workflow: AgentWorkflow, role: str = "", ts: str = "") -> str:
    """LLM 不可用时, 用工作流自带的章节骨架生成结构化简报 (每部分标注来源编号)。"""
    lines: list[str] = []
    lines.append(f"# 账户情报简报 · {_core_of(question, 40)}")
    lines.append("")
    lines.append(
        f"> 工作流：{workflow.id}（{workflow.name}） · 生成角色：{role or '-'} · 生成时间：{ts or '-'}"
    )
    lines.append("> 声明：本简报由检索式 agent 依据可访问文档自动生成，结论须人工复核后使用。")
    lines.append("")

    for idx, (title, keywords) in enumerate(workflow.report_sections, start=1):
        lines.append(f"## {['一', '二', '三', '四', '五', '六'][idx - 1] if idx <= 6 else idx}、{title}")
        if idx == 1:
            for s in _parse_account_signals(question):
                lines.append(f"- {s}")
        # 按章节关键词在合并来源里选最相关的 2 条作为依据
        kw = set(tokenize(keywords))
        ranked = sorted(
            enumerate(sources),
            key=lambda p: len(kw & set(tokenize(p[1][0].content + " " + p[1][0].title))),
            reverse=True,
        )
        picked = [(i + 1, c) for i, (c, _s) in ranked[:2]]
        if not picked:
            lines.append("- 资料未覆盖，需人工补充")
        for num, c in picked:
            lines.append(
                f"- 依据 来源{num}（{getattr(c, 'doc_source', DEFAULT_DOC_SOURCE)} "
                f"{c.path}）：{_snippet(c.content)}"
            )
        lines.append("")

    if workflow.human_decisions:
        lines.append("## 需人工决策")
        for d in workflow.human_decisions:
            lines.append(f"- [ ] {d}")
        lines.append("")

    lines.append("## 来源引用")
    if not sources:
        lines.append("- 无（未检索到可访问文档）")
    for i, (c, s) in enumerate(sources, start=1):
        lines.append(
            f"{i}. [{getattr(c, 'doc_source', DEFAULT_DOC_SOURCE)}] {c.path} :: {c.title} · {s:.3f}"
        )
    return "\n".join(lines)

# ============================================================
# 审计日志 (SQLite)
# ============================================================

class AuditLog:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def _conn(self):
        # WAL + busy_timeout: ThreadingHTTPServer 多线程并发写审计时避免 "database is locked"
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    user TEXT NOT NULL,
                    role TEXT NOT NULL,
                    question TEXT NOT NULL,
                    source_docs TEXT NOT NULL,
                    answer TEXT,
                    answer_mode TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    user TEXT NOT NULL,
                    question TEXT NOT NULL,
                    vote INTEGER NOT NULL
                )
            """)

    def log(self, user: User, question: str,
            sources: list[tuple[Chunk, float]], answer: str, mode: str,
            ts: str | None = None):
        ts = ts or _now_iso()
        src_json = json.dumps(
            [{"path": c.path, "title": c.title, "score": round(s, 4),
              "doc_id": c.doc_id} for c, s in sources],
            ensure_ascii=False,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, user, role, question, source_docs, answer, answer_mode) "
                "VALUES(?,?,?,?,?,?,?)",
                (ts, user.username, user.role, question, src_json, answer, mode),
            )

    def recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, user, role, question, source_docs, answer, answer_mode "
                "FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"ts": r[0], "user": r[1], "role": r[2], "question": r[3],
             "source_docs": r[4], "answer": r[5], "answer_mode": r[6]}
            for r in rows
        ]

    def log_feedback(self, user: User, question: str, vote: int):
        """点赞/点踩 (1/-1): 运营闭环的反馈入口, 与审计同库。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback(ts, user, question, vote) VALUES(?,?,?,?)",
                (_now_iso(), user.username, question, int(vote)),
            )

    def feedback_stats(self) -> dict:
        """反馈聚合: 总数 + 有用/无用分布。"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            up = conn.execute("SELECT COUNT(*) FROM feedback WHERE vote=1").fetchone()[0]
            down = conn.execute("SELECT COUNT(*) FROM feedback WHERE vote=-1").fetchone()[0]
        return {"total": int(total), "up": int(up), "down": int(down)}

    def stats(self) -> dict:
        """可观测聚合: 总量 / 模式分布 / 用户分布 / top 文档 (最近 500 条来源内解析)。"""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            by_mode = dict(conn.execute(
                "SELECT answer_mode, COUNT(*) FROM audit_log GROUP BY answer_mode"
            ).fetchall())
            by_user = dict(conn.execute(
                "SELECT user, COUNT(*) FROM audit_log GROUP BY user ORDER BY COUNT(*) DESC"
            ).fetchall())
            rows = conn.execute(
                "SELECT source_docs FROM audit_log ORDER BY id DESC LIMIT 500"
            ).fetchall()
        doc_hits: dict[str, int] = {}
        for (raw,) in rows:
            try:
                for s in json.loads(raw):
                    path = str(s.get("path", "?"))
                    doc_hits[path] = doc_hits.get(path, 0) + 1
            except Exception:
                continue
        top_docs = sorted(doc_hits.items(), key=lambda p: -p[1])[:3]
        return {
            "total": int(total),
            "by_mode": {str(k): int(v) for k, v in by_mode.items()},
            "by_user": {str(k): int(v) for k, v in by_user.items()},
            "top_docs": [{"path": p, "hits": h} for p, h in top_docs],
        }

def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat()

# ============================================================
# 知识库门面 (Facade)
# ============================================================

class KnowledgeBase:
    """把摄入/索引/检索/生成/审计串起来, 并提供参数化 agent 工作流。"""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.from_env()
        self.chunks: list[Chunk] = []
        self.embedder = None
        self.bm25 = BM25Index()
        self.retriever: Retriever | None = None
        self.generator = Generator(self.cfg)
        self.audit = AuditLog(self.cfg.db_path)
        self._built = False

    def build(self) -> None:
        """摄入文档并构建索引。"""
        ingestor = MarkdownIngestor(self.cfg.kb_root, self.cfg.chunk_size, self.cfg.chunk_overlap)
        self.chunks = ingestor.ingest()
        token_lists = [tokenize(c.content) for c in self.chunks]
        # 向量后端选择
        if self.cfg.use_dense_embeddings and self.cfg.embed_base_url:
            self.embedder = DenseEmbedder(
                self.cfg.embed_base_url, self.cfg.embed_api_key, self.cfg.embed_model
            )
            self.embedder.fit([c.content for c in self.chunks])
        else:
            self.embedder = TfidfEmbedder().fit(token_lists)
        self.bm25.fit(token_lists)
        self.retriever = Retriever(
            self.chunks, self.embedder, self.bm25,
            alpha=self.cfg.alpha, top_k=self.cfg.top_k,
            min_score=self.cfg.min_score, candidate_pool=self.cfg.candidate_pool,
        )
        self._built = True

    # ---- 单跳 (保持原行为不变) ----

    def ask(self, question: str, user: User, top_k: int | None = None) -> Answer:
        if not self._built:
            self.build()
        assert self.retriever is not None
        sources = self.retriever.search(question, user, top_k)
        answer_text, mode = self.generator.answer(question, sources)
        ts = _now_iso()
        self.audit.log(user, question, sources, answer_text, mode, ts)
        return Answer(
            question=question, answer=answer_text, mode=mode,
            sources=[_source_dict(c, s) for c, s in sources],
            user=user.username, ts=ts, sub_queries=[question],
        )

    # ---- 多跳 agent ----

    def ask_agent(self, question: str, user: User,
                  workflow_id: str = DEFAULT_WORKFLOW_ID,
                  top_k: int | None = None) -> Answer:
        """参数化多跳 agent: 规划 -> 逐子问题检索(各自过 RBAC) -> 合并去重 -> 合成。

        workflow_id 决定规划/合成的 prompt 与离线章节骨架, 因此同一套引擎可以
        驱动不同业务 agent (通用知识问答 / GTM 账户情报)。
        """
        if not self._built:
            self.build()
        assert self.retriever is not None

        wf = WORKFLOWS.get(str(workflow_id or "").strip()) or WORKFLOWS[DEFAULT_WORKFLOW_ID]
        k = top_k or self.cfg.top_k

        sub_queries = self._plan_sub_questions(question, user, wf, k)
        merged = self._multi_hop_search(question, sub_queries, user, k,
                                        authoritative=wf.sub_queries_authoritative)

        ts = _now_iso()
        if not merged:
            answer_text, mode = NO_CONTEXT_ANSWER, "no_context"
        else:
            answer_text, mode = self._synthesize(question, merged, wf)

        # 审计: question 记原问题, mode 记 llm/extractive/no_context, 表结构不变
        self.audit.log(user, question, merged, answer_text, mode, ts)
        return Answer(
            question=question, answer=answer_text, mode=mode,
            sources=[_source_dict(c, s) for c, s in merged],
            user=user.username, ts=ts, sub_queries=list(sub_queries),
        )

    def _plan_sub_questions(self, question: str, user: User,
                            wf: AgentWorkflow, k: int) -> list[str]:
        """规划步: LLM 优先, 失败/不可用则走确定性离线规划。"""
        subs: list[str] = []
        if self.generator.llm_ready() and self.cfg.plan_mode != "offline":
            try:
                subs = _parse_json_array(self.generator.plan(question, wf))
            except Exception:
                subs = []
        if not subs and self.cfg.plan_mode != "llm":
            subs = self._offline_plan(question, user, wf, k)
        subs = _dedup_strs(subs, wf.max_sub_questions)
        if not subs:
            subs = [question]        # 兜底: 退化为单跳
        # 若子问题本身就是原问题的切片 (并列拆法), 不必再补一跳原问题
        sliced = any(s in question for s in subs)
        if question not in subs and not sliced and len(subs) < wf.max_sub_questions:
            subs.insert(0, question)  # 保留原问题这一跳, 避免拆偏
        return subs[: wf.max_sub_questions]

    def _offline_plan(self, question: str, user: User,
                      wf: AgentWorkflow, k: int) -> list[str]:
        """离线规划: 维度骨架 -> 并列切分 -> 命中标题深挖, 保证确定性。"""
        core = _core_of(question)
        if wf.offline_aspects:                    # 工作流自带的维度骨架 (如 GTM 四维度)
            if wf.aspect_with_core:
                return [f"{core} {a}" for a in wf.offline_aspects]
            return list(wf.offline_aspects)

        parts = _split_question(question)
        if len(parts) >= 2:                       # 「A 是多少，B 是什么」这类多意图
            return parts

        # 通用问答: 先探测一次; 只有「未被直接命中」时才做第二跳深挖 (数据驱动),
        # 简单问题保持单跳, 行为与改造前一致。
        seeds = self.retriever.search(question, user, k * 4)  # type: ignore[union-attr]
        if not _needs_multi_hop(question, seeds):
            return [question]
        aspects = _aspects_from_hits(seeds, wf.max_sub_questions - 1)
        subs: list[str] = [question] + [f"{core} {a}" for a in aspects]
        if len(subs) < 2:                          # 仍拆不开就明确退化为单跳
            subs = [question]
        return subs

    def _multi_hop_search(self, question: str, sub_queries: list[str], user: User,
                          k: int, authoritative: bool = False) -> list[tuple[Chunk, float]]:
        """执行步: 每个子问题独立检索 (RBAC 在 retriever 内逐条生效), 再合并去重。

        两种模式都: RBAC 在子问题层级生效; 低于 min_score 的来源在合并时即剔除,
        保证 LLM 合成上下文与 web 门过滤后的来源列表一致 (回答里的来源编号
        不会指向列表里不存在的条目)。

        关于「防拆题跑偏」: 旧口径下分数是 min-max 归一化后的相对分, 跨查询不
        可比, 只能靠「深挖分不得超过原问题对该文档的分数」(cap) 和「原问题没
        召回过的文档不许混入」两条补丁来压制。改写为绝对可比的 relevance 之后,
        min_score 门本身就承担了这个职责——不相关文档的 relevance 天然低, 因此
        这两条补丁一并移除:
          - cap 会误伤真正的深挖成果 (长句原问题的自匹配上界很高, 把 base 分整体
            压低, 连带把子问题命中的高相关章节也 cap 到很低, 实测只剩 1 条来源);
          - 「原问题未召回不许混入」与多跳初衷相悖, 子问题的价值恰恰在于找到
            原问题没直接命中的角度。
        """
        min_score = self.cfg.min_score
        # 原问题同样先做口语词 -> 文档用词的同义改写 (如 打折->折扣)
        base = self.retriever.search(_rewrite_query(question), user, k * 2)  # type: ignore[union-attr]

        best: dict[tuple[str, str], tuple[Chunk, float]] = {}
        for c, s in base:
            if s >= min_score:
                best[(c.path, c.title)] = (c, s)
        for q in sub_queries:
            for c, s in self.retriever.search(_rewrite_query(q), user, k):  # type: ignore[union-attr]
                key = (c.path, c.title)
                if key in best:
                    continue                       # 已在原问题那一跳里, 保留原分
                if s >= min_score:                 # relevance 绝对可比, 直接采用
                    best[key] = (c, s)
        merged = sorted(best.values(), key=lambda p: -p[1])
        # authoritative 模式 (gtm_research) 四维子问题各自贡献多条来源,
        # 窗口放宽到 k*3 避免高价值章节 (如折扣审批权限) 被截断
        return merged[: (k * 3 if authoritative else k * 2)]

    def _synthesize(self, question: str, sources: list[tuple[Chunk, float]],
                    wf: AgentWorkflow) -> tuple[str, str]:
        """合成步: LLM 优先 (用工作流的 synth_prompt), 否则离线结构化 / 抽取式回退。"""
        if self.generator.llm_ready() and self.cfg.plan_mode != "offline":
            try:
                return self.generator.synthesize(question, sources, wf.synth_prompt), "llm"
            except Exception as e:
                return self._offline_answer(question, sources, wf) + f"\n\n(LLM 不可用: {e})", "extractive"
        return self._offline_answer(question, sources, wf), "extractive"

    def _offline_answer(self, question: str, sources: list[tuple[Chunk, float]],
                        wf: AgentWorkflow) -> str:
        """离线合成: 有章节骨架则出结构化简报, 否则退回抽取式摘要。"""
        if wf.report_sections:
            return build_offline_report(question, sources, wf)
        return Generator._extractive(question, sources)


# 命令行入口: python kb_qa.py "问题" --user alice --role viewer --dirs public
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="最小知识库问答系统 / Agent 工作流")
    ap.add_argument("question", nargs="?", default=None)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--role", default="admin", choices=["admin", "sales", "viewer"])
    ap.add_argument("--dirs", default="", help="受限角色可访问目录, 逗号分隔")
    ap.add_argument("--kb", default=None, help="知识库根目录")
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--agent", action="store_true", help="走多跳 agent (ask_agent)")
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW_ID, choices=sorted(WORKFLOWS))
    args = ap.parse_args()

    cfg = Config.from_env()
    if args.kb:
        cfg.kb_root = args.kb

    allowed = tuple(d.strip() for d in args.dirs.split(",") if d.strip()) if args.dirs else ()
    user = User(username=args.user, role=args.role, allowed_dirs=allowed)

    kb = KnowledgeBase(cfg)
    if args.question:
        ans = kb.ask_agent(args.question, user, workflow_id=args.workflow,
                           top_k=args.top_k) if args.agent else kb.ask(args.question, user, top_k=args.top_k)
        print(f"=== 问题 ===\n{ans.question}\n")
        if args.agent:
            print("=== 子问题 (%d) ===" % len(ans.sub_queries))
            for i, q in enumerate(ans.sub_queries, 1):
                print(f"  {i}. {q}")
            print()
        print(f"=== 来源 ({ans.mode}) ===")
        for s in ans.sources:
            print(f"  - [{s['score']:.3f}] [{s.get('source')}] {s['path']} :: {s['title']}")
        print(f"\n=== 回答 ===\n{ans.answer}\n")
    else:
        # 无问题: 打印审计日志
        print(f"已摄入 {len(kb.chunks)} 个分块。最近审计日志:")
        for row in kb.audit.recent(10):
            src = json.loads(row["source_docs"])
            src_paths = ", ".join(s["path"] for s in src) or "-"
            print(f"  {row['ts']} | {row['user']}({row['role']}) | {row['question']} | src: {src_paths}")
