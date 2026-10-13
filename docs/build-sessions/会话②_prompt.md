# WorkBuddy 会话② prompt（10/10 晚）

## 背景

延续会话①的成果，现有三个文件：

1. `kb_qa.py`（667 行，本机已验证）：Markdown 摄入分块、BM25+TF-IDF 混合检索、RBAC、SQLite 审计日志。**关键类**：`KnowledgeBase` 实例上有 `kb.audit`（`AuditLog`），提供 `kb.audit.recent(limit=20)` → 返回 `list[dict]`，每条 `{ts, user, role, question, source_docs(JSON字符串), answer, answer_mode}`。
2. `web_ui.py`（服务端，标准库 http.server，端口 8000）：已有 `GET /`（返回 index.html）、`GET /api/health`、`POST /api/ask`。
3. `static/index.html`（企业蓝风单页聊天界面）：顶部用户名+角色下拉（admin/viewer）、消息区、来源引用、底部输入框。

## 任务（本次范围，两个改动）

### 改动 1：新增审计日志接口

在 `web_ui.py` 新增 `GET /api/audit?limit=20&role=admin`：
- 调 `self.kb.audit.recent(limit)` 取最近记录，转 JSON 返回（source_docs 保持原字符串即可，或解析后返回，前端只需数来源条数）。
- **RBAC：`role` 非 `admin`（含缺省）一律返回 403 JSON** `{"error": "审计日志仅 admin 可见"}`——这是治理演示点，不要放开。
- limit 缺省 20，上限 100。

### 改动 2：index.html 增加审计面板 + chips 换成与本地文档匹配的问题

**审计面板**：
- header 右侧新增「审计日志」按钮，**仅当前角色为 admin 时显示**（切到 viewer 自动隐藏）。
- 点击打开模态框/抽屉：表格列 = 时间 | 用户 | 角色 | 问题 | 来源数 | 回答模式，按时间倒序，可滚动，右上角「刷新」按钮，点外部或 × 关闭。
- 数据来自 `/api/audit?limit=20&role=<当前角色>`；viewer 下不请求。
- 样式延续现有企业蓝风（#2563EB accent、白卡片、圆角 12px、字体变量与现有 CSS 一致）。

**chips 替换**（当前 4 个 chips 里「报销流程」「数据分级」在本地文档里不存在，点了会空命中，必须换掉）。本地文档只有 4 篇：
- `public/faq.md`：环境变量配置、RBAC 配置、审计日志存在哪里
- `public/getting-started.md`：安装（pip install numpy requests）、运行（python kb_qa.py）
- `internal/roadmap.md`：Q3 计划、薪酬带宽（初级 15-20k 等）
- `internal/ops.md`：数据库备份（凌晨 2 点）、故障处理、密钥管理

换成这 4 个 chips（前两个 public 后两个 internal，正好演示 RBAC）：
1. `系统怎么安装运行？`（→ getting-started）
2. `审计日志存在哪里？`（→ faq）
3. `薪酬带宽是多少？`（→ roadmap，viewer 会被拦）
4. `数据库什么时候备份？`（→ ops，viewer 会被拦）

## 硬约束

1. 只用 Python 标准库 + 内嵌 HTML/CSS/JS，**禁止新增 pip 依赖**。
2. **不重写 kb_qa.py**，只改 `web_ui.py` 和 `static/index.html`。
3. 不破坏现有功能：`/api/ask`、`/api/health`、聊天界面行为保持不变。
4. 代码风格与现有文件一致：类型注解、中文注释、`from __future__ import annotations`。

## 输出要求

1. `web_ui.py` 完整代码（全文，含新增 `/api/audit`）
2. `static/index.html` 完整代码（全文，含审计面板 + 新 chips）
3. 两条 curl 验收命令（admin 取审计记录 + viewer 403）
4. 一句运行说明（文件放哪、怎么启动）

## 验收标准

- `GET /api/audit?limit=5&role=admin` 返回最近问答记录 JSON（字段齐全）
- `GET /api/audit?role=viewer` 返回 403
- 浏览器 admin 身份点「审计日志」看到表格；切 viewer 按钮消失
- 4 个 chips 点击后 admin 全部命中（前两个 public、后两个 internal）；viewer 点后两个应被 RBAC 拦（no_context）
