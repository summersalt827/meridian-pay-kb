# WorkBuddy 会话① prompt（10/8 晚）

## 任务

我已附加 `kb_qa.py`（667 行，本机已验证可跑的知识库问答系统：Markdown 摄入分块、BM25+TF-IDF 混合检索、RBAC、SQLite 审计日志，仅依赖 numpy + 标准库）。请在其基础上**新增一个本地 Web 聊天界面**，让用户在浏览器里直接问答知识库。

## 硬约束

1. **只用 Python 标准库**（`http.server`）实现服务端，前端用内嵌 HTML/CSS/JS 单页。**禁止新增 pip 依赖**（沙箱环境只有 numpy，没有 Flask/FastAPI）。
2. **不重写 kb_qa.py 核心逻辑**，通过 `from kb_qa import KnowledgeBase, User, Config` 复用现有类。
3. 新文件：`web_ui.py`（服务端，放 kb_qa.py 同目录）+ `static/index.html`（前端单页）。
4. 监听端口 8000，`localhost:8000` 可访问。
5. 代码风格与 kb_qa.py 保持一致：类型注解、dataclass、中文注释、`from __future__ import annotations`。

## 功能清单（本次范围）

1. **单页聊天界面**：消息列表（用户消息靠右、助手回答靠左，气泡样式）、底部输入框、按钮发送 + 回车发送、发送后清空输入框并显示「思考中」占位。
2. **顶部小表单**：用户名输入框（默认 admin）+ 角色下拉（admin/viewer）。viewer 角色固定映射为 `allowed_dirs=("public",)`，admin 为全部可见。
3. **后端接口**：
   - `POST /api/ask`：接收 JSON `{question, username, role}` → 构造 `User` → 调 `kb.ask()` → 返回 `{answer, mode, sources: [{path, title, score}], ts}`。
   - `GET /api/health`：返回 `{built: true, chunks: <摄入分块数>}`。
   - 服务启动时自动执行 `kb.build()`（懒加载也可，但 health 必须能报出真实分块数）。
4. **回答展示**：回答正文下方列出来源引用（`path :: title` + score 保留 3 位小数）+ 回答模式标签（llm=蓝 / extractive=橙 / no_context=灰）。
5. 界面为简洁企业风：白底、蓝色 accent（#2563EB 系）、顶部一条产品名 **Enterprise Knowledge Brain**（占位名，后续可改）。

## 输出要求

1. `web_ui.py` 完整代码（全文）
2. `static/index.html` 完整代码（全文，CSS/JS 内嵌）
3. 启动命令与自测命令（curl 验证 `/api/health` 和 `/api/ask` 各一条）
4. 简短说明：文件放在哪个目录、如何运行

## 验收标准

- `python web_ui.py` 后浏览器打开 http://localhost:8000 能正常聊天
- admin 问「薪酬带宽」应命中 internal/roadmap.md（来源列表可见）
- 切到 viewer 问同一问题应返回 no_context 或空来源（RBAC 拦截生效）
- `/api/health` 返回的分块数 > 0
