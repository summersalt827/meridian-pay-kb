# WorkBuddy 会话④ prompt（10/12 晚）

## 背景

延续会话①②③。系统现状（沙箱与本机一致，除注明外）：

1. `kb_qa.py`（~700 行）：Markdown 摄入分块（按标题切分+重叠）、BM25+TF-IDF 混合检索、RBAC（User.role + allowed_dirs 目录级权限）、SQLite 审计、LLM 回退（OpenAI 兼容 /chat/completions，已接 DeepSeek 实测可用）。核心类：`KnowledgeBase`（`ask(question, user)`、`retriever.search(query, user)`、`generator`（`Generator`：`_call_llm(query, ctx)` 私有方法 + `answer(query, sources)`）、`audit`）。`User(username, role, allowed_dirs)`。**⚠️ 沙箱版 kb_qa.py 没有 min_score 字段**（相关性阈值由 web 层实现，见下条）；本机版 kb_qa 在 retriever 层有 min_score，落地时会合并——你在沙箱里不要依赖 kb 层的 min_score。
2. `web_ui.py`：标准库 http.server，端口 8000。`GET /`、`GET /api/health`、`POST /api/ask`（角色 admin/sales/viewer；viewer=("public",)，sales=("public","gtm")，admin 不限）、`GET /api/audit`（仅 admin 403 治理）。**⚠️ 沙箱的 min_score=0.3 相关性门在 web 层**：`WebConfig.min_score`（环境变量 KB_MIN_SCORE 可覆盖）+ `_gate_sources()`（过滤低于门限的来源，全被过滤则 mode=no_context）——你的改动不得破坏这道门。
3. `static/index.html`：企业蓝风聊天界面，三角色下拉、「审计日志」抽屉（仅 admin）、6 个 chips。**⚠️ 品牌已是 Meridian Pay**：title「Meridian Pay · 知识库问答」、logo「MP」、标题「Meridian Pay 知识大脑」、副标题「SME 商务卡 · 跨境付款 · 多币种钱包」、欢迎语「欢迎使用 Meridian Pay 知识大脑」、欢迎区说明「Meridian Pay 内部知识库 · 产品 / 合规 / 销售知识按角色分级开放，回答附带来源引用与权限判定。」、header 两个绿色合规徽章「L1–L3 数据分级已生效」「MAS 审计留痕开启」——输出完整 index.html 时上述品牌元素必须原样保留。
4. `sample_docs/`：public/（faq.md 报销休假、getting-started.md 入职指南）+ internal/（roadmap.md 路线图+薪酬、ops.md 资金结算 SOP+合规）+ gtm/（sales-playbook.md、pricing.md、case-studies.md、icp-targeting.md），共 8 篇，B2B fintech 语境（公司名 Meridian Pay：SME 企业卡+跨境支付+多币种钱包，MAS/PDPA 合规，SGD 报价）。⚠️ public 文档已做词面去重叠调优（tokenize 是中文单字+bigram，跨目录同词会泄漏分数），本次**不改文档正文**；若你新增示例文档，避免使用「账户/结算/折扣/薪酬」等跨目录高频词。**来源平台标注走代码常量映射（见改动 1 第 6 条），绝不写入文档正文**——重复平台词会污染检索词面。
5. `test_rbac.py`：金标准评测（黄金集 8 例，断言 leak rate=0 + recall=100%）。

## 本次目标（大改）

把系统从「单工作流问答」升级为 **参数化 Agent 工作流平台**（呼应赛题「Enterprise Agent Builder」）：

1. **多跳 agent 能力**：LLM 规划 → 拆解子问题 → 每个子问题独立检索（各自过 RBAC）→ 合并去重 → LLM 合成带引用答案。UI 展示拆解出的子问题（思考过程可视化）。
2. **工作流参数化**：内置两个工作流——`knowledge_qa`（通用知识问答）与 `gtm_research`（GTM 客户调研简报生成，复用同一套多跳骨架、不同的规划/合成 prompt）。这是「平台」叙事的关键：同一引擎驱动不同业务 agent。

## 任务（三个改动）

### 改动 1：kb_qa.py 新增工作流 + 多跳方法（不破坏现有 ask）

新增 `AgentWorkflow` dataclass（id、name、plan_prompt、synth_prompt、max_sub_questions）与 `WORKFLOWS` 注册表，内置两个：

- `knowledge_qa`：规划 prompt=「把用户问题拆成可独立检索的子问题，最多 3 个；简单问题直接返回只含原问题的数组」；合成 prompt=沿用现有严谨问答风格（只依据来源、不可编造、末尾标注来源编号）
- `gtm_research`（对外展示名「**账户情报**」，对齐 Aspire GTM Systems Builder JD 的「Operationalise AI：账户情报」）：规划 prompt=「按 信号层判断 / ICP 分层与路由 / 套餐与折扣 / 客户案例与合规边界 四个维度拆解客户调研请求，最多 4 个」；合成 prompt=输出「**账户情报简报**」结构：公司概况与信号判断（融资/招聘/市场扩张）→ ICP 分层判断与**路由建议**（自助/SDR/AE 直跟）→ 推荐套餐与折扣权限说明 → 相关案例与话术 → 下一步建议（sequencing 入口），每部分标注来源编号，**简报末尾附「需人工决策」清单**（如折扣是否特批、是否触发合规评估——呼应 JD 的「agent 自主运行 vs 人工在环 + guardrails」：agent 完成 80%，需要人拍板的事项明确列出）

在 `KnowledgeBase` 新增 `ask_agent(self, question, user, workflow_id="knowledge_qa", top_k=None) -> Answer`：

1. **规划步**：LLM 可用 → 用 workflow.plan_prompt 调 LLM 要求返回 JSON 字符串数组（如 `["企业客户的分层路由规则是什么", "新客户上线流程有哪些步骤"]`）；解析失败或 LLM 不可用 → 回退 `[原问题]`。**子问题数组只有 1 个元素（含回退）时视为单跳**（agent 未实际生效）。
2. **执行步**：对每个子问题独立调用 `self.retriever.search(sub_q, user, top_k)`（RBAC 在每个子问题层级生效）；合并结果，按 (path, title) 去重保留最高分，分数降序，截断到 top_k*2。
3. **合成步**：合并后 sources 为空 → mode=no_context（answer=「未检索到相关文档, 无法回答。」）。否则 LLM 用 workflow.synth_prompt 生成最终答案（要求标注来源编号）；LLM 不可用 → 抽取式回退。
4. **审计**：照常 `self.audit.log(...)`（question=原问题，mode=llm/extractive/no_context），**不改审计表结构**。
5. **Answer 增加字段** `sub_queries: list[str] = field(default_factory=list)`：ask_agent 填入实际使用的子问题列表（含回退时的 [原问题]）；现有 ask() 填空列表即可（或 [question]，你来定，保持前端兼容）。
6. **来源平台标注（connector 叙事，呼应题目「跨 Confluence/Jira/Slack/Drive 检索」）**：在 kb_qa.py 新增常量 `DOC_SOURCES: dict[str, str]`（相对路径 → 平台名），映射如下：faq.md→Confluence、getting-started.md→Confluence、roadmap.md→Confluence、ops.md→Confluence、sales-playbook.md→Drive、pricing.md→Drive、case-studies.md→Drive、icp-targeting.md→Jira（键用 `public/faq.md` 这类相对 sample_docs 的完整路径，避免重名）。ingest 时把平台名挂到 chunk（新增 `doc_source` 字段），检索结果的 Source 对象新增 `source` 字段（缺省 `"Confluence"`，向后兼容）。**文档文件本身不改**——平台标注只存在于代码常量，检索词面零污染。

### 改动 2：web_ui.py 暴露 agent 与 workflow

- `POST /api/ask` 新增可选字段：`"agent": true/false`（缺省 true；false 时走现有 ask() 单跳，供回归测试用）、`"workflow": "knowledge_qa"/"gtm_research"`（缺省 knowledge_qa；非法值 400；agent=false 时忽略）
- `_handle_ask`：agent=true 时调 `ask_agent`；响应 JSON 新增 `"agent"`（本次是否多跳生效：子问题数>1）、`"workflow"`、`"sub_queries"`（实际子问题列表）字段；**最终 sources 必须照旧过 `_gate_sources` 相关性门**；sources 数组每项透传 `source` 平台字段
- **可观测**：`_handle_ask` 用 `time.perf_counter()` 包住 ask/ask_agent 调用，响应 JSON 新增 `"elapsed_ms": int`（含 LLM 调用全链路耗时）
- `/api/health` 增加 `"workflows": ["knowledge_qa", "gtm_research"]`

### 改动 3：index.html 适配（工作流选择器 + 思考过程 + 品牌保持）

- 欢迎区 chips 上方加**工作流分段选择器**（两个按钮：「知识问答」「账户情报」，默认前者）。选中「账户情报」时 chips 区替换为 1 个示例问题 chip：「一家 400 人、月均 300 笔跨境付款的外贸公司刚完成 B 轮融资、计划拓展东南亚，帮我做账户情报分析并给路由建议」（参数确保确定性：>300 人=企业客户层、>200 笔=AE 直跟、B 轮融资=高优先级信号）；切回知识问答恢复原 6 chips。发送时 payload 带 `workflow` 字段。
- **账户情报简报回答气泡加「下载简报.md」按钮**（前端 Blob 下载，纯 JS 把 answer 文本存为 md 文件，无后端改动）——体现 agent「产出文件」而非只回答。
- 回答气泡：若 `data.agent && data.sub_queries.length > 1`，在回答正文**上方**显示「查询规划」块（浅灰底小字，子问题编号 ①②③，复用 .sources 视觉语言）；`sub_queries` 只有 1 个时不显示。
- meta 区加 workflow 标签：gtm_research 显示「GTM 调研」pill（样式参考 .pill.sales 的橙色调即可）。
- **来源平台标注显示**：引用列表每项在文件名前显示平台小徽章（Confluence 蓝 / Drive 绿 / Jira 橙，与 .pill 视觉语言一致），如「[Confluence] roadmap.md · 0.92」；「下载简报.md」按钮生成的简报里每条来源编号行同样带平台名（如 `[1] pricing.md（Drive）`）。
- **可观测显示**：meta 区（mode 标签旁）加一行小字「⏱ {elapsed_ms/1000 保留 1 位}s · {sources.length} 来源」，每次回答后随 data.elapsed_ms 更新；no_context 也显示（来源数 0）。
- ⚠️ **品牌保持 Meridian Pay**：title/logo「MP」/标题「Meridian Pay 知识大脑」/副标题「SME 商务卡 · 跨境付款 · 多币种钱包」/欢迎语/欢迎区说明/两个绿色合规徽章「L1–L3 数据分级已生效」「MAS 审计留痕开启」——输出完整 index.html 时不得改回通用品牌（Enterprise Knowledge Brain/EK），上述徽章与文案必须原样保留。

## 硬约束

1. 只用 Python 标准库 + numpy + 内嵌 HTML/CSS/JS，禁止新增 pip 依赖（requests 已在 kb_qa 使用）。
2. 不破坏现有 `ask()` 单跳方法、`/api/audit` 403 逻辑、审计抽屉、6 chips（知识问答模式下）、min_score 阈值行为（沙箱里该行为由 web 层 `_gate_sources` 实现；agent 模式返回的 sources 也必须经过这道门）。
3. RBAC 必须在子问题层级生效：sales 拆解出的子问题若涉及 internal/ 内容，该子问题检索结果为空——**不允许**任何子问题绕过 allowed_dirs。
4. 代码风格与现有文件一致：类型注解、中文注释、`from __future__ import annotations`。
5. **来源平台标注只存在于 `DOC_SOURCES` 代码常量，禁止写进文档正文或 chunk 文本**（重复英文平台词会污染 BM25/TF-IDF 检索词面，破坏语料调优）；检索/合成/审计逻辑完全不受 source 字段影响。
6. elapsed_ms 只做透传展示，不参与任何逻辑分支。

## 输出要求

1. `kb_qa.py` 完整代码（含 AgentWorkflow/WORKFLOWS/ask_agent）
2. `web_ui.py` 完整代码
3. `static/index.html` 完整代码
4. 四条 curl 验收命令：①admin 多跳问题看 sub_queries 拆解 ②sales 越权问题看子层级 RBAC ③workflow=gtm_research 客户调研 ④agent=false 基线（结果应与改造前一致）
5. 一句运行说明

## 验收标准

- admin 问「新客户从签约到上线要多久？」（knowledge_qa）→ agent=true、sub_queries 含 2-3 个子问题、命中 gtm 多篇文档、回答带来源编号
- sales 问「薪酬带宽是多少，打折规则是什么？」→ 薪酬子问题被 RBAC 拦（该子问题无结果），gtm 子问题命中、可回答打折部分（RBAC 子层级生效）——注意这里必须是 sales（viewer 连 gtm 都不可见，两个子问题都会落空）
- 简单问题（如「报销流程是什么？」）→ sub_queries 只有 1 个、界面不显示规划块，行为与改造前一致
- **workflow=gtm_research**（admin 或 sales）问「一家 400 人、月均 300 笔跨境付款的外贸公司刚完成 B 轮融资、计划拓展东南亚，帮我做账户情报分析并给路由建议」→ agent=true、sub_queries 按 信号/ICP 路由/套餐/案例 维度拆解、输出账户情报简报（信号判断：B 轮融资+拓东南亚=高优先级 / 分层：企业客户层 / 路由：AE 直跟 / 推荐套餐 / 折扣权限 / 案例话术 + 末尾「需人工决策」清单），每部分带来源编号，回答气泡有「下载简报.md」按钮
- agent=false 时结果与改造前完全一致；`test_rbac.py` 仍然全过（单跳 ask 未被破坏）
- 任意回答的引用列表带平台徽章（如「[Confluence] roadmap.md」），meta 区显示「⏱ 1.3s · 3 来源」；简报下载文件的来源行带平台名
- `test_rbac.py` 黄金集断言逻辑不受 source 字段影响（Source 新增字段向后兼容）
