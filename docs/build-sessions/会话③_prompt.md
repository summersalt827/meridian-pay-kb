# WorkBuddy 会话③ prompt（10/11 晚）

## 背景

延续会话①②。现有文件（本机已验证可跑）：

1. `kb_qa.py`：Markdown 摄入分块、BM25+TF-IDF 混合检索、RBAC（User.role + allowed_dirs 目录级权限）、SQLite 审计日志、**min_score=0.3 相关性阈值**（RBAC 过滤后最佳来源低于 0.3 视为无命中）、LLM 回退（OpenAI 兼容 /chat/completions，已接 DeepSeek 实测 llm 模式正常）。
2. `web_ui.py`：标准库 http.server，端口 8000。已有 `GET /`、`GET /api/health`、`POST /api/ask`（viewer 固定 allowed_dirs=("public",)，admin 不限）、`GET /api/audit`（仅 admin，其余 403）。`WebConfig.to_kb_config()` 用 `Config.from_env()` 透传 LLM 环境变量。
3. `static/index.html`：企业蓝风单页聊天界面。顶部用户名+角色下拉（admin/viewer）+「审计日志」按钮（仅 admin 显示）+ 审计抽屉（表格+刷新+Esc/点遮罩关闭）。chips 现有 4 个（安装运行/审计位置/薪酬带宽/数据库备份）。
4. `sample_docs/`：public/（faq.md、getting-started.md）+ internal/（roadmap.md、ops.md）。
5. `test_rbac.py`：金标准评测（黄金集 6 例，断言 leak rate=0 + recall=100%），已全过。

## 本次目标（大改）

把 demo 场景从「通用软件公司」整体换成 **B2B fintech 公司（给中小企业提供企业卡 + 跨境支付服务，对标 Aspire）**。理由：赛道评委是 Aspire 团队，demo 场景必须让评委看到自己的业务。同时升级 RBAC 为三角色（viewer < sales < admin）。

## 任务（四个改动）

### 改动 0：重写现有 4 篇文档为 fintech 语境（全部替换）

虚构公司背景：一家 B2B fintech（SME 企业卡 + 跨境支付 + 多币种账户）。文档要求：内容具体、可检索、互相有区分度（避免用词雷同导致检索混淆）。

- `public/faq.md` → **内部行政 FAQ**（全员可见）：报销流程与时限、休假政策、设备申请、办公系统入口
- `public/getting-started.md` → **新员工入职指南**：账号开通、工位/设备领取、第一周培训清单
- `internal/roadmap.md` → **产品路线图 + 机密信息**（仅 admin）：2025 Q4 产品计划（多币种账户/企业卡虚拟卡）、**薪酬带宽**（初级 15-20k / 高级 25-40k / 架构师 45-60k，机密）
- `internal/ops.md` → **资金账户操作手册 + 合规要求**（仅 admin）：结算/对账 SOP（每日结算时间点）、备用金审批权限、**PDPA/MAS 审计留痕要求**（所有客户数据操作必须可追溯）、数据分级政策

### 改动 1：生成 GTM 文档集（4 篇，放入 sample_docs/gtm/，仅 sales 及以上可见）

同公司语境（B2B fintech 销售团队知识库）：

1. `gtm/sales-playbook.md`：销售 SOP——开场白、需求挖掘提问清单、报价审批流程、合同审批 SLA、赢单/丢单复盘
2. `gtm/pricing.md`：定价——套餐（Starter/Growth/Enterprise）、价格与功能边界、折扣审批权限（销售总监 15%、CEO 30%）、年度合同优惠
3. `gtm/case-studies.md`：客户案例 2-3 个——客户行业/规模、痛点、方案、量化结果（如「上线 3 个月回款周期缩短 40%」）
4. `gtm/icp-targeting.md`：ICP 分层——SMB/中端/企业三层画像、高价值信号（融资/招聘扩张/新市场进入）、分层路由规则（自助/SDR/AE）

### 改动 2：web_ui.py 支持第三角色 sales

- `/api/ask` 角色校验放行 `sales`：`allowed_dirs=("public", "gtm")`（viewer 仍 ("public",)，admin 仍不限）
- `/api/audit` 维持仅 admin（sales 也 403）
- `/api/health` 的 roles 更新为 `["admin", "sales", "viewer"]`

### 改动 3：index.html 适配（三角色 + 6 chips）

- 角色下拉加 sales 选项（顺序 admin/sales/viewer）
- roleText：sales = 「sales 可检索 public/ 与 gtm/ 目录」
- chips 全部换新，共 6 个（每对演示一层权限）：
  1. `报销流程是什么？`（→ public/faq，三角色都命中）
  2. `新员工怎么领电脑设备？`（→ public/getting-started，三角色都命中）
  3. `薪酬带宽是多少？`（→ internal/roadmap，仅 admin 命中，sales/viewer 被拦）
  4. `资金账户结算流程是什么？`（→ internal/ops，仅 admin，被拦同上）
  5. `大客户报价折扣能批到几折？`（→ gtm/pricing，sales/admin 命中，viewer 被拦）
  6. `企业客户的分层路由规则是什么？`（→ gtm/icp-targeting，同上）
- 审计按钮仍仅 admin 显示

## 硬约束

1. 只用 Python 标准库 + 内嵌 HTML/CSS/JS，禁止新增 pip 依赖
2. 不重写 kb_qa.py；只改 web_ui.py、static/index.html、重写 sample_docs/ 全部 8 篇 md
3. 不破坏现有功能（/api/audit 403 逻辑、审计抽屉、min_score 阈值行为）
4. 代码风格与现有文件一致：类型注解、中文注释、`from __future__ import annotations`

## 输出要求

1. 8 篇文档全文（各放一个代码块，标注文件名，改动 0 的 4 篇 + 改动 1 的 4 篇）
2. `web_ui.py` 完整代码
3. `static/index.html` 完整代码
4. 三条 curl 验收命令（sales 问定价命中 gtm / viewer 同问被拦 / sales 调审计 403）
5. 一句运行说明

## 验收标准

- sales 问「大客户报价折扣能批到几折？」→ 命中 gtm/pricing.md
- viewer 问同一句 → no_context 被拦
- sales/viewer 问「薪酬带宽」→ no_context 被拦，admin 命中
- sales 调 /api/audit → 403
- admin 全部 6 个 chips 均命中（public×2 / internal×2 / gtm×2）
