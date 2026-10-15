# Aspire Requirements Map — challenge brief → Meridian Pay Knowledge Brain

Every requirement in the Aspire *Enterprise Agent Builder & Autonomous Operations Platform* brief (FinTech Track, Handbook §5), mapped to the mechanism that solves it, where it is specified, and the artifact that proves it.

## Problem statement → mechanism

| Brief pain point | Meridian Pay mechanism | Where | Visible artifact |
|---|---|---|---|
| Teams rely on multiple systems, workflows, data sources; building production-grade agents is complex | One parameterised multi-hop skeleton (plan → sub-question retrieval → merge → synthesise) drives multiple business agents; a workflow is defined purely as plan/synthesis prompts in a registry — configuration-as-markdown, no code per agent | `WORKFLOWS` registry `kb_qa.py:209` | Workflow selector + 6 preset-question chips in the UI |
| Agents need more than a chatbot: secure build / deploy / monitor / manage | Real auth (PBKDF2 200k + HMAC-signed tokens), RBAC at directory *and* sub-question level, SQLite audit trail (WAL), feedback loop | `auth.py` · `kb_qa.py` · `test_auth.py` | Login screen; audit drawer with aggregate dashboard |
| Enterprise knowledge scattered across Confluence / Jira / Drive; plain RAG cannot answer "who may see what" | Hybrid retrieval (BM25 + TF-IDF) with an **absolute relevance gate** calibrated on 27 labelled samples (irrelevant max 0.1521 < 0.17 < relevant min 0.2580, margins 1.12x/1.52x); off-topic → clean `no_context`, never a fabrication | `kb_qa.py` relevance gate · `test_relevance.py` | Designed no-context card: "证据不足 · 已拒绝回答" |
| GTM teams spend hours per account on manual research | `gtm_research` workflow: signals → ICP routing → discount tier → case-law compliance → first-touch email draft → human-decisions list | `gtm_research` workflow prompts | Account Intelligence briefing (2–3 h → ~30 s), downloadable `.md` |

## "What the Solution Should Solve" → coverage

| Brief requirement | Coverage | Where | Visible artifact |
|---|---|---|---|
| Build & configure agents, automations, multi-step workflows via a reusable internal platform | `WORKFLOWS` registry: `knowledge_qa` + `gtm_research` share one skeleton; new agent = new plan/synthesis prompts + report sections | `kb_qa.py:209` | Workflow selector; two live agents in one engine |
| Connect agents securely to enterprise systems, APIs, databases, third-party platforms | Connector architecture: `MarkdownIngestor` connector demonstrated, source badges mapped in code constants for Confluence / Drive / Jira — reserved slots for the four big sources named in the brief | `kb_qa.py` source-badge mapping | Source citation cards with platform badges |
| Manage credentials & secrets without exposing them to agents or end users | Secrets stay in environment variables (`LLM_API_KEY`, `KB_AUTH_SECRET`, `sync: false`, not in repo); tokens are server-issued HMAC, roles never client-declared | `render.yaml` · `auth.py` | Deploy guide; 401/403 contract tests |
| **RBAC** so agents only access information permitted for the relevant user or role | Directory-level roles across 7 fintech personas (admin: all · sales: public+gtm · viewer: public · compliance_officer: public+compliance · risk_analyst: +risk · card_ops: +card_ops · finance_analyst: +finance) enforced **at the sub-question level** — an out-of-scope sub-question retrieves nothing, so a decomposed query cannot fish out restricted documents. Pre-retrieval ACL filtering (OWASP LLM08 recommended control), not post-hoc filtering | `kb_qa.py` RBAC · `web_ui.py` role map · `test_rbac.py` | Gold-standard eval: **leak rate = 0, recall loss = 0**; demo shots: sales blocked on internal, viewer blocked on gtm, card_ops blocked on compliance |
| Comprehensive security logs, execution histories, audit trails | Every Q&A logged to SQLite (WAL, concurrency-safe): user, role, question, answer, mode, sources, latency, feedback; admin-only audit API + aggregate dashboard | `kb_qa.py` audit · `test_web_matrix.py` | Audit drawer (admin): totals, mode distribution, active users, feedback, top docs |
| Observability into agent actions, decisions, failures, latency, cost, performance | Multi-hop planning block visualises sub-questions; per-answer `elapsed_ms`; cost measured per briefing (~¥0.012 on DeepSeek chat); audit aggregates queries per user & per mode | `static/index.html` planning block · audit dashboard | Planning block in chat; audit aggregates; cost figures in description |
| Reusable development standards & best practices for production agents | One skeleton = one enforced standard; `AGENTS.md` in repo; ten test suites (337 assertions) as the quality gate — every change must pass the matrix | `AGENTS.md` · 10 test suites | All-green test output; eval section below |
| Recover from failures, retry, escalate exceptions, self-healing | Transient LLM failures auto-retry; no-Key fallback to extractive mode (offline answer from retrieved chunks); every briefing ends with a **human-decisions list** (discount sign-off 15%/30%, compliance review, email approval) — escalation is a designed output, not an error path | `kb_qa.py` retry/fallback · `human_decisions` in workflow | "需人工决策" list in Account Intelligence briefing |
| Enterprise knowledge layer across multiple internal platforms **respecting existing permissions** | Same RBAC that gates the answer gates every retrieval: sub-question-level ACL + absolute relevance threshold; cross-directory hybrid retrieval with mixed CN/EN tokenisation (single char + bigram) | `kb_qa.py` · `test_rbac.py` · `test_relevance.py` | Sales hits public+gtm only; viewer hits public only — matrix 18 role pairs |
| Measure product & operational metrics for agent effectiveness and business impact | Feedback loop (helpful/unhelpful votes into SQLite) closes the loop; audit dashboard = ops metrics; briefings state estimated hours saved; cost per call measured | `/api/feedback` · audit dashboard | Feedback buttons in UI; hours-saved line in briefing |

## "The Solution Should Be" → coverage

| Quality | How Meridian Pay delivers it |
|---|---|
| Developer-Friendly | A new agent = write plan/synthesis prompts in the registry; 6 preset question chips lower adoption friction; `AGENTS.md` documents the standard |
| Flexible | Two business agents on one engine (knowledge QA + GTM account intelligence); connector slots reserved for Confluence/Jira/Drive/Slack |
| Secure by Design | PBKDF2-HMAC-SHA256 (200k iters + salt), HMAC-signed tokens with jti revocation & constant-time compare, anti-enumeration for unknown users, sub-question-level RBAC, 401/403 contract |
| Governed & Auditable | Full audit trail + aggregate dashboard; human-decisions list with approval tiers (Sales Director 15%, CEO 30%, >30% rejected); feedback loop |
| Observable | Planning block, latency, sources, mode labels, audit aggregates, cost per call |
| Reliable & Self-Healing | Auto-retry on transient LLM faults; extractive offline fallback; clean `no_context` instead of confident errors |
| Context-Aware | Hybrid BM25+TF-IDF over all permitted directories; platform badges show provenance; multi-hop decomposition for compound questions |
| Production-Ready | Render blueprint one-click deploy; SQLite WAL for concurrent access; API contract tests; known boundary stated honestly (single-keyword lexical ceiling → cross-encoder rerank is the production path) |
| Scalable | Directory-based permission matrix extends to new teams/roles (`allowed_dirs`); one registry hosts N workflows; connector architecture adds sources without touching the agent skeleton |

## Demonstrated workflows (brief: "at least one practical workflow")

We demonstrate **two**:

| Brief workflow | Our demonstration |
|---|---|
| **Internal Knowledge Brain** (Confluence/Jira/Slack/Drive + RBAC + audit) | `knowledge_qa` — multi-hop retrieval with sub-question RBAC, source citations, platform badges, no-context rejection card, audit trail. Demo scenario ② as stated in README |
| **GTM Engineering** (source, enrich, engage, qualify, hand off, feedback loops) | `gtm_research` — account intelligence briefing: signal scoring card (Fit/Timing/Access/Intent → tier → SLA, backed by a signal library with Definition/Why-predicts-fit/Hook per signal), ICP routing (self-serve/SDR/AE + PARK verdicts), LAARC objection playbook (budget/authority/need/timing), first-touch email draft with PDPA/CAN-SPAM notes, human-decisions list; feedback loop via votes |

## FinTech 赛道 7 能力 → worked example（官方手册）

| 官方要求 | 状态 | Meridian Pay 机制 | Worked example / 可见产物 |
|---|---|---|---|
| 1. 统一自然语言查询（单一有引用的答案） | ✅ 完整 | 多跳规划 → 子问题检索 → 合并合成，每个来源带路径/平台/分数 | 体验路径步骤 2：admin 命中 + 来源引用卡片 |
| 2. 权限感知检索（先进过滤，后进 LLM；不压平平台权限模型） | ⚠️ **部分** | 子问题级 RBAC（pre-retrieval ACL，OWASP LLM08 推荐控制）+ **audience 披露门**（客户在场模式：audience=external 之外的分块逐候选剔除，代码常量分级、不读文档自我声明）；角色只由服务端令牌签发 | 金标评测 **leak=0**；步骤 3：sales 同问被干净拦截；步骤 6：同一问题内部 4 文档 → 客户在场仅 case-studies（test_disclosure 11/11）<br>**边界**：过滤机制已实现并被测到 leak=0，但连接器是插槽，未在真实平台权限模型上端到端验证（语料带 `·demo` 标记，API 返回 `demo_corpus: true`） |
| 3. 数据新鲜度（有界窗口，分钟~1 小时） | ✅ 完整 | `POST /api/resync`（admin）按需重扫 + `KB_RESYNC_MINUTES` 定时重扫 + 引用卡片带文档 mtime「更新于」 | 步骤 9：改文档 → 重扫 → 新内容可答；test_web_matrix R1-R5（含删除后不再可答） |
| 4. 负面权限案例（不泄露存在性） | ✅ 完整 | 越权子问题检索为空；相关性门过滤后 `no_context` 拒绝卡（「证据不足 · 已拒绝回答」）；对外语境下内部文档既不进答案、也不进来源列表 | 步骤 3/8：被拦时只有拒绝卡，无来源列表；客户在场问折扣 → 干净 no_context |
| 5. 实时权限变更（吊销即时生效） | ⚠️ **部分** | 令牌内嵌角色，校验时与账号当前角色比对（`auth.py` verify_token）——角色一变，旧令牌立即 401 | test_auth 23 攻击面用例（改 payload 即验签失败）<br>**边界**：**角色**变更即时生效；**文档级**权限变更依赖 resync 窗口，无每查询回源校验 |
| 6. 审计查询（防篡改、可重建） | ✅ 完整 | SQLite WAL 全量审计 + **append-only 触发器 + SHA-256 哈希链**（重算验证可定位篡改行，test_audit_chain 6/6）；admin-only API + 聚合看板 | 步骤 7：审计抽屉「验证链」按钮 |
| 7. LLM 泄露缓解（防幻觉/编造） | ✅ 完整 | 回答只基于过门来源（faithfulness 100%，0 幻觉引用）；无来源宁拒不留；**盾牌答案复检**：出模型后再扫一遍，模型凭空编出的卡号/账号同样被拦下 | 红队 26 项攻击零泄漏（[RED-TEAM-REPORT](RED-TEAM-REPORT.md)）；`test_shield.py` C5 |

**超出官方 7 项的补充**（Aspire 是持牌金融机构，PII 是硬要求，手册未单列）：

| 补充要求 | Meridian Pay 机制 | Worked example / 可见产物 |
|---|---|---|
| **A. need-to-know 最小知情（PDPA 目的限制）** | 盾牌在文本进 LLM **之前**掩码卡号 / NRIC / FIN / IBAN / 账号 / 手机 / 邮箱 / 出生日期 / 护照 / 地址 / 凭证（凭证对 admin 也掩码）；靠 Luhn / mod-97 **校验**而非长度猜测 | `scripts/demo_shield.py`：8 类标识全变标签；`test_shield.py` A1–A10 + 反向断言 A2/A4′ |
| **B. 间接提示注入防护（OWASP LLM01）** | 整行剔除指令行（中英文）、中和伪造的 `[来源N]` 标签、清除零宽字符；我们的来源前缀在盾牌**之后**才拼上，因此文档无法伪造上下文边界 | `scripts/demo_shield.py`：2 行注入被剔除、业务结论「已全额退款」完整保留 |
| **C. 审计库自身不成为泄露面** | 审计落**全掩码**的问题与答案，只记「规则与计数」，绝不记明文 | `test_shield.py` C4（审计库不含明文卡号/NRIC）+ C4′（链仍可验证） |

## Judging dimensions → evidence (Handbook §10, 10 × 10 pts)

| Dimension | Evidence |
|---|---|
| Impact & Relevance | Demo speaks Aspire's own business language, in Aspire's own order: **KYC/AML** onboarding file checklist and regulatory inspection list, suspicious-transaction reporting deadlines, **corporate-card** issuance and card-status rules, **spend-management** and travel standards, **transaction-monitoring** thresholds — each mapped 1:1 to one of the seven role accounts (compliance officer / risk analyst / card ops / finance analyst / sales / new-hire / internal audit); plus fund-settlement SOP with MAS/PDPA obligations and GTM account research 2–3 h → ~30 s supporting the challenge's 25% SQL outbound goal; scattered enterprise knowledge is the named pain point |
| Human-Centered Design | Seven walkable accounts mapped to real fintech personas — internal-audit (admin), compliance officer, risk analyst, card operations, finance analyst, sales/account-manager, new-hire/external — plus a GTM-team persona on the Account Intelligence workflow; 6 preset chips for high-frequency questions; honest no-context card instead of confident fabrication; briefings end with a human-decisions list — the agent does 80%, people keep the decision rights |
| AI Interaction | Multi-hop planning (visible sub-questions) → per-sub-question retrieval → merge → synthesis; two agents from one parameterised skeleton; prompts (not code) define each workflow; dual fallback (LLM → extractive) |
| Technical Execution | Ten test suites, 337 assertions, all green (2026-10-15) + **CI on every push**: RBAC gold standard leak=0 / recall-loss=0; relevance threshold 0.17 calibrated on 27 labelled samples with complete class separation; 23 auth attack-surface cases; 49 web matrix cases incl. 8 fintech-role + 5 data-freshness (resync) cases; **26-check red team suite — 0 leaks across semantic rewrites, prompt injection, path traversal, role forgery, multi-hop puzzles, and threshold suppression** (see [RED-TEAM-REPORT](RED-TEAM-REPORT.md)); **audit hash chain** — append-only triggers + SHA-256 re-verification pinpoints tampered rows; **disclosure gate** — customer-present mode with external-context zero leak + zero miss, per-sub-question; **shield** — 32 assertions incl. paired assertions (a 16-digit number failing Luhn must NOT be masked; normal business text must NOT be over-stripped) — proving precision, not blunt suppression |
| Feasibility | Real auth (PBKDF2/HMAC), real persistence (SQLite WAL), Render blueprint deploy; connector architecture maps 1:1 to real platforms; honest boundary stated (lexical-retrieval ceiling) |
| Demo & Storytelling | 8-beat script: login → admin hit → **role-switch block (wow moment)** → audit drawer → GTM briefing → all-green eval → quantified close; demo-path e2e proof script (`scripts/demo_path.py`, 12/12) |
| Innovation & Creativity | Sub-question-level RBAC (decomposed queries cannot fish out restricted docs — pre-retrieval ACL, not post-filter); absolute relevance gate (BM25 / query self-match ceiling) makes "off-topic" a measurable decision instead of a magic number; **"by measurement, not by construction"** — attacked, not asserted |
| UX & Accessibility | Purple-red brand hero, workflow selector, planning block, platform badges, downloadable briefing, audit dashboard; WCAG 2.2 AA contrast measured from real hex values with fixes applied, aria-live thread + aria-pressed workflow state — see [UX-ACCESSIBILITY-AUDIT](UX-ACCESSIBILITY-AUDIT.md) |
| Responsible AI & Ethics | 0 role violations, 0 PII leaks, faithfulness 100% (0 hallucinated citations) on industry eval axes; **need-to-know before the model**: identifiers masked before retrieval text reaches the LLM (PDPA purpose limitation + minimisation), and masked again in the answer; **the audit log stores fully masked questions and answers**, so compliance evidence is not a second leak surface; **retrieved content is data, not instruction** — indirect prompt injection stripped before the LLM; audit trail doubles as MAS FEAT accountability evidence and MAS Notice 626 STR-readiness documentation; refusal-over-fabrication by design |
| Overall Quality & Judge's Impression | Complete README + this requirements map + evaluation tables + deploy config + 12 WorkBuddy build screenshots + 8 live-demo captures; end-to-end reproducible (`pip install` → run → login as alice/sam/bob) |

## Submission traceability (Handbook §7)

| Requirement | Status |
|---|---|
| Built on CodeBuddy / WorkBuddy | ✅ WorkBuddy: foundation run (9/26) + sessions ①–⑤ — prompts & conversation log in `docs/build-sessions/`, 12 chat screenshots in `证据/`, written process description in `docs/AI-COLLABORATION.md` |
| Chat history ≥3 screenshots | ✅ 12 provided |
| Blurb <10 words | ✅ "Ask anything. RBAC decides. Shield masks. You audit." (8 words) — 全仓库统一此句，README / 提交文案 / 本表三处一致 |
| Cover 16:9 380×216 | ✅ `证据/封面图_16-9_380x216.png` |
| Project link (bonus) | ✅ Live at https://meridian-pay-kb.onrender.com (deployed 2026-10-15; health + login + RBAC verified live) |
| Mocked integrations labelled | ✅ 平台徽章一律带 `·demo` 后缀，`/api/health` 返回 `demo_corpus: true`，README 单列「已知边界」——不把演示语料包装成实时连接器 |
| Demo video 5–8 min (optional) | Script drafted (8 beats); recording planned 10/15 |
