# AGENTS.md — AI 协作指南

给 AI 编码助手（Claude Code / WorkBuddy / CodeBuddy）的项目约定。

## 运行与测试

- 启动：`python3 web_ui.py`（LLM 模式需 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`；鉴权默认 token 模式，演示账号 alice/sam/bob 口令 meridian-demo）
- **提交前必跑九套**：`python3 test_rbac.py && python3 test_relevance.py && python3 test_auth.py && python3 test_web_matrix.py && python3 test_redteam.py && python3 test_audit_chain.py && python3 test_disclosure.py && python3 test_shield.py && python3 test_gtm_scorecard.py`（全绿才可提交；CI 会跑同样九套共 227 断言）
- 前端改动后做 JS 冒烟：提取 `<script>` 内容跑 `node --check`
- 盾牌改动后跑对照演示自检：`python3 scripts/demo_shield.py`（自带 8 条自检）

## 硬约束

1. 仅标准库 + numpy + requests，禁止新增 pip 依赖（shield.py 同理，纯标准库）
2. 改动语料（sample_docs/）后必跑全套回归；新增词不得引入新的跨目录高频词对（tokenize 是中文单字 + bigram，跨目录同词会泄分）
   - **盾牌演示语料放 `demo_docs/`，不要放进 `sample_docs/`**：它含 PII 与注入文本，且会影响 idf 与相关性标定
3. 改 min_score（0.17）前必重跑 test_relevance.py 的分可性断言（下侧余量 1.1x+）
4. 来源平台标注只存在于 kb_qa.DOC_SOURCES 代码常量，禁止写入文档正文；且平台徽章必须带 `·demo` 后缀（DEMO_CORPUS），不得伪装成实时连接器
5. 品牌保持 Meridian Pay：紫红渐变 UI（#7C3AED→#DB2777）、合规徽章、平台徽章不得回退企业蓝
6. **盾牌只收窄、从不放宽**：新增脱敏规则必须同时补「不该掩码的别掩码」反向断言（参考 test_shield.py 的 A2 / A4′ / B7）
7. **盾牌在进 LLM 之前生效**：任何新增的检索→生成路径（含离线抽取式回退）都必须过 `shield.sanitize_for_llm`，否则等于开了一条绕过口
8. **评分卡不得交回模型打分**：gtm_research 的 Fit/Timing/Access/Intent 与 tier 由 `scorecard.py` 按 icp-targeting.md §6 确定性算出后注入 prompt；改动评分规则必须同步 §6 文档与 test_gtm_scorecard.py 的档位断言
9. **缺事实 ≠ 不追**：评分卡里「事实齐全但低分 → 不追」与「事实不全 → 待补事实」是两种终止，不可合并；前者是判断，后者是没问清楚。合并会把谨慎伪装成安全
8. 审计落库的是**全掩码**的问题与答案；审计只记「规则与计数」，禁止记被掩码的明文
9. `self_ceiling()` 里 OOV 词抬高上界是**刻意保留**的（去掉会让无关提问分数从 0.15 涨到 0.73，阈值失效），不要「优化」掉它

## 架构速查

- `kb_qa.py`：摄入分块（按标题链）→ 混合检索（两段式：hybrid 召回候选 → relevance 绝对分过门 + RBAC）→ AgentWorkflow 注册表（plan/synth prompt 参数化，gtm_research 带 extra_aspects 确定性补全）→ 多跳（规划 → 子问题各自检索过 RBAC → 合并去重 → **盾牌** → 合成 → **答案复检**）→ 审计/反馈
- `scorecard.py`：GTM 确定性评分卡。按 icp-targeting.md §6 算四维分 → tier/SLA/路由；红线（§7）优先于分数；缺事实记 0 且标「需人工补充」，部分缺事实时声明总分为下限
- `shield.py`：盾牌（第三道闸）。`sanitize_for_llm()` = 剔除指令行 → 掩码标识（进 LLM 前）；`rescan_answer()` = 出模型后复检；`mask_identifiers()` 靠 Luhn/mod-97 **校验**判定，不是长度猜测
- relevance = BM25 分 / 查询自匹配上界（跨查询可比，0.17 由 27 组样本标定）
- `auth.py`：PBKDF2-HMAC-SHA256 口令 + HMAC 签名令牌 + jti 吊销（角色变更即作废）
- `web_ui.py`：Bearer 令牌鉴权 + 限流（`KB_RATE_LIMIT`/`KB_LOGIN_RATE_LIMIT`，默认 60/10 每分钟）+ 审计/反馈 API
- 语料 8 篇：public 2（全员）/ internal 2（仅 admin）/ gtm 4（sales+admin：icp 含评分卡规则与不追清单、playbook 含异议表与跟进节奏、pricing、case-studies 含量化案例）
