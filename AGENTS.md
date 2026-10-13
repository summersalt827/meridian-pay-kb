# AGENTS.md — AI 协作指南

给 AI 编码助手（Claude Code / WorkBuddy / CodeBuddy）的项目约定。

## 运行与测试

- 启动：`python3 web_ui.py`（LLM 模式需 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`；鉴权默认 token 模式，演示账号 alice/sam/bob 口令 meridian-demo）
- **提交前必跑四套**：`python3 test_rbac.py && python3 test_relevance.py && python3 test_auth.py && python3 test_web_matrix.py`（全绿才可提交）
- 前端改动后做 JS 冒烟：提取 `<script>` 内容跑 `node --check`

## 硬约束

1. 仅标准库 + numpy + requests，禁止新增 pip 依赖
2. 改动语料（sample_docs/）后必跑四套回归；新增词不得引入新的跨目录高频词对（tokenize 是中文单字 + bigram，跨目录同词会泄分）
3. 改 min_score（0.17）前必重跑 test_relevance.py 的分可性断言（下侧余量 1.1x+）
4. 来源平台标注只存在于 kb_qa.DOC_SOURCES 代码常量，禁止写入文档正文
5. 品牌保持 Meridian Pay：紫红渐变 UI（#7C3AED→#DB2777）、合规徽章、平台徽章不得回退企业蓝

## 架构速查

- `kb_qa.py`：摄入分块（按标题链）→ 混合检索（两段式：hybrid 召回候选 → relevance 绝对分过门 + RBAC）→ AgentWorkflow 注册表（plan/synth prompt 参数化，gtm_research 带 extra_aspects 确定性补全）→ 多跳（规划 → 子问题各自检索过 RBAC → 合并去重 → 合成）→ 审计/反馈
- relevance = BM25 分 / 查询自匹配上界（跨查询可比，0.17 由 27 组样本标定）
- `auth.py`：PBKDF2-HMAC-SHA256 口令 + HMAC 签名令牌 + jti 吊销（角色变更即作废）
- `web_ui.py`：Bearer 令牌鉴权 + 限流（`KB_RATE_LIMIT`/`KB_LOGIN_RATE_LIMIT`，默认 60/10 每分钟）+ 审计/反馈 API
- 语料 8 篇：public 2（全员）/ internal 2（仅 admin）/ gtm 4（sales+admin：icp 含评分卡规则与不追清单、playbook 含异议表与跟进节奏、pricing、case-studies 含量化案例）
