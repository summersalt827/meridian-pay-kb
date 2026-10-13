# Aspire Requirements Map — challenge brief → Meridian Pay Knowledge Brain

Every requirement in the Aspire *Enterprise Agent Builder & Autonomous Operations Platform* brief (FinTech Track, Handbook §5), mapped to the mechanism that solves it, where it is specified, and the artifact that proves it.

## Problem statement → mechanism

| Brief pain point | Meridian Pay mechanism | Where | Visible artifact |
|---|---|---|---|
| Teams rely on multiple systems, workflows, data sources; building production-grade agents is complex | One parameterised multi-hop skeleton (plan → sub-question retrieval → merge → synthesise) drives multiple business agents; a workflow is defined purely as plan/synthesis prompts in a registry — configuration-as-markdown, no code per agent | `WORKFLOWS` registry `kb_qa.py:209` | Workflow selector + 6 preset-question chips in the UI |
| Agents need more than a chatbot: secure build / deploy / monitor / manage | Real auth (PBKDF2 200k + HMAC-signed tokens), RBAC at directory *and* sub-question level, SQLite audit trail (WAL), feedback loop | `auth.py` · `kb_qa.py` · `test_auth.py` | Login screen; audit drawer with aggregate dashboard |
| Enterprise knowledge scattered across Confluence / Jira / Drive; plain RAG cannot answer "who may see what" | Hybrid retrieval (BM25 + TF-IDF) with an **absolute relevance gate** calibrated on 27 labelled samples (irrelevant ≤0.145, relevant ≥0.244); off-topic → clean `no_context`, never a fabrication | `kb_qa.py` relevance gate · `test_relevance.py` | Designed no-context card: "证据不足 · 已拒绝回答" |
| GTM teams spend hours per account on manual research | `gtm_research` workflow: signals → ICP routing → discount tier → case-law compliance → first-touch email draft → human-decisions list | `gtm_research` workflow prompts | Account Intelligence briefing (2–3 h → ~30 s), downloadable `.md` |

## "What the Solution Should Solve" → coverage

| Brief requirement | Coverage | Where | Visible artifact |
|---|---|---|---|
| Build & configure agents, automations, multi-step workflows via a reusable internal platform | `WORKFLOWS` registry: `knowledge_qa` + `gtm_research` share one skeleton; new agent = new plan/synthesis prompts + report sections | `kb_qa.py:209` | Workflow selector; two live agents in one engine |
| Connect agents securely to enterprise systems, APIs, databases, third-party platforms | Connector architecture: `MarkdownIngestor` connector demonstrated, source badges mapped in code constants for Confluence / Drive / Jira — reserved slots for the four big sources named in the brief | `kb_qa.py` source-badge mapping | Source citation cards with platform badges |
| Manage credentials & secrets without exposing them to agents or end users | Secrets stay in environment variables (`LLM_API_KEY`, `KB_AUTH_SECRET`, `sync: false`, not in repo); tokens are server-issued HMAC, roles never client-declared | `render.yaml` · `auth.py` | Deploy guide; 401/403 contract tests |
| **RBAC** so agents only access information permitted for the relevant user or role | Directory-level roles (admin: all · sales: public+gtm · viewer: public) enforced **at the sub-question level** — an out-of-scope sub-question retrieves nothing, so a decomposed query cannot fish out restricted documents. Pre-retrieval ACL filtering (OWASP LLM08 recommended control), not post-hoc filtering | `kb_qa.py` RBAC · `test_rbac.py` | Gold-standard eval: **leak rate = 0, recall loss = 0**; demo shots: sales blocked on internal, viewer blocked on gtm |
| Comprehensive security logs, execution histories, audit trails | Every Q&A logged to SQLite (WAL, concurrency-safe): user, role, question, answer, mode, sources, latency, feedback; admin-only audit API + aggregate dashboard | `kb_qa.py` audit · `test_web_matrix.py` | Audit drawer (admin): totals, mode distribution, active users, feedback, top docs |
| Observability into agent actions, decisions, failures, latency, cost, performance | Multi-hop planning block visualises sub-questions; per-answer `elapsed_ms`; cost measured per briefing (~¥0.012 on DeepSeek chat); audit aggregates queries per user & per mode | `static/index.html` planning block · audit dashboard | Planning block in chat; audit aggregates; cost figures in description |
| Reusable development standards & best practices for production agents | One skeleton = one enforced standard; `AGENTS.md` in repo; four test suites (81 assertions) as the quality gate — every change must pass the matrix | `AGENTS.md` · 4 test suites | All-green test output; eval section below |
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
| **GTM Engineering** (source, enrich, engage, qualify, hand off, feedback loops) | `gtm_research` — account intelligence briefing: signal scoring card (Fit/Timing/Access/Intent → tier → SLA), ICP routing (self-serve/SDR/AE), discount & case-law compliance, first-touch email draft with PDPA/CAN-SPAM notes, human-decisions list; feedback loop via votes |

## Judging dimensions → evidence (Handbook §10, 10 × 10 pts)

| Dimension | Evidence |
|---|---|
| Impact & Relevance | GTM account research drops 2–3 h → ~30 s, directly supporting the challenge's 25% SQL outbound goal; enterprise knowledge scattered across Confluence/Jira/Drive is the named pain point |
| Human-Centered Design | Three real roles walkable in the demo (admin/sales/viewer); 6 preset chips for high-frequency questions; honest no-context card instead of confident fabrication; briefings end with a human-decisions list — the agent does 80%, people keep the decision rights |
| AI Interaction | Multi-hop planning (visible sub-questions) → per-sub-question retrieval → merge → synthesis; two agents from one parameterised skeleton; prompts (not code) define each workflow; dual fallback (LLM → extractive) |
| Technical Execution | Five test suites, 107 assertions, all green (2026-10-13) + **CI on every push**: RBAC gold standard leak=0 / recall-loss=0; relevance threshold 0.17 calibrated on 27 labelled samples with complete class separation; 23 auth attack-surface cases; 29 web matrix cases; **26-check red team suite — 0 leaks across semantic rewrites, prompt injection, path traversal, role forgery, multi-hop puzzles, and threshold suppression** (see [RED-TEAM-REPORT](RED-TEAM-REPORT.md)) |
| Feasibility | Real auth (PBKDF2/HMAC), real persistence (SQLite WAL), Render blueprint deploy; connector architecture maps 1:1 to real platforms; honest boundary stated (lexical-retrieval ceiling) |
| Demo & Storytelling | 8-beat script: login → admin hit → **role-switch block (wow moment)** → audit drawer → GTM briefing → all-green eval → quantified close; demo-path e2e proof script (`scripts/demo_path.py`, 12/12) |
| Innovation & Creativity | Sub-question-level RBAC (decomposed queries cannot fish out restricted docs — pre-retrieval ACL, not post-filter); absolute relevance gate (BM25 / query self-match ceiling) makes "off-topic" a measurable decision instead of a magic number; **"by measurement, not by construction"** — attacked, not asserted |
| UX & Accessibility | Purple-red brand hero, workflow selector, planning block, platform badges, downloadable briefing, audit dashboard; WCAG 2.2 AA contrast measured from real hex values with fixes applied, aria-live thread + aria-pressed workflow state — see [UX-ACCESSIBILITY-AUDIT](UX-ACCESSIBILITY-AUDIT.md) |
| Responsible AI & Ethics | 0 role violations, 0 PII leaks, faithfulness 100% (0 hallucinated citations) on industry eval axes; audit trail doubles as MAS FEAT accountability evidence and MAS Notice 626 STR-readiness documentation; PDPA purpose limitation via role-scoped retrieval; refusal-over-fabrication by design |
| Overall Quality & Judge's Impression | Complete README + this requirements map + evaluation tables + deploy config + 12 WorkBuddy build screenshots + 8 live-demo captures; end-to-end reproducible (`pip install` → run → login as alice/sam/bob) |

## Submission traceability (Handbook §7)

| Requirement | Status |
|---|---|
| Built on CodeBuddy / WorkBuddy | ✅ WorkBuddy: foundation run (9/26) + sessions ①–⑤ — prompts & conversation log in `docs/build-sessions/`, 12 chat screenshots in `证据/`, written process description in `docs/AI-COLLABORATION.md` |
| Chat history ≥3 screenshots | ✅ 12 provided |
| Blurb <10 words | ✅ "Enterprise knowledge, one trusted source with RBAC." (7 words) |
| Cover 16:9 380×216 | ✅ `证据/封面图_16-9_380x216.png` |
| Project link (bonus) | Render blueprint ready; live URL to be confirmed before submission |
| Demo video 5–8 min (optional) | Script drafted (8 beats); recording planned 10/15 |
