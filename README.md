# Meridian Pay 知识大脑 (KB-QA Agent Platform)

腾讯云 AI CAN DO IT Hackathon 2026 · FinTech 赛道（Aspire 出题）
**Enterprise Agent Builder & Autonomous Operations Platform** — 演示场景 ② Internal Knowledge Brain

## 是什么

一个参数化 Agent 工作流平台：同一套「规划 → 多子问题检索(各自过 RBAC) → 合并 → 合成」多跳骨架，
通过不同的规划/合成 prompt 驱动不同业务 agent：

- **knowledge_qa** 知识问答：LLM 拆解子问题 → 逐子问题检索 → 合并合成带引用答案
- **gtm_research** 账户情报：按 信号层 / ICP 路由 / 套餐折扣 / 案例合规 四维拆解，
  输出结构化账户情报简报 + 「需人工决策」清单（human-in-the-loop）

## 核心能力

- **混合检索**：BM25 + TF-IDF 向量，中英文混合分词（中文单字 + 二字 bigram）
- **RBAC**：目录级权限（admin 全部 / sales public+gtm / viewer 仅 public），
  多跳模式下权限在**子问题层级**生效——越权子问题检索为空，不泄漏
- **审计留痕**：SQLite 全量问答日志，/api/audit 仅 admin 可见（403 治理）
- **相关性门**：min_score 阈值（retriever 层 + web 层双保险），防止 RBAC 过滤后低分文档凑数
- **来源平台标注**：Confluence / Drive / Jira 平台徽章（connector 叙事，仅代码常量，不污染检索词面）
- **可观测**：查询规划块（子问题可视化）、耗时、来源引用
- **金标准评测**：test_rbac.py 黄金集 8 例断言 **leak rate = 0 + recall = 100%**

## 运行

```bash
pip install -r requirements.txt
python3 web_ui.py          # http://localhost:8000
```

LLM 模式（OpenAI 兼容接口，无 Key 自动回退抽取式）：

```bash
LLM_BASE_URL=https://api.deepseek.com LLM_API_KEY=sk-xxx LLM_MODEL=deepseek-chat python3 web_ui.py
```

## 测试

```bash
python3 test_rbac.py        # 金标准: leak=0 + recall=100% (独立测试库)
python3 test_web_matrix.py  # Web 层 28 用例: 18 组角色矩阵 + 审计权限 + agent 用例 (需服务在跑)
```

## API

- `POST /api/ask` `{question, username, role, agent, workflow}` → answer/mode/sources/sub_queries/elapsed_ms
- `GET /api/audit?role=admin&limit=20` → 审计日志（非 admin 403）
- `GET /api/health` → 索引状态 + 可用工作流

## 部署 (Render)

`render.yaml` Blueprint 一键部署。`LLM_API_KEY` 在 Render 环境变量里手动填写（sync: false，不入仓库）。
免费档 15 分钟无访问会休眠，用 UptimeRobot 每 10 分钟 ping `/api/health` 保活。

## 目录

```
kb_qa.py            主系统（摄入/混合检索/RBAC/审计/多跳 agent/工作流注册表）
web_ui.py           Web 服务（标准库 http.server，0.0.0.0 + PORT 环境变量）
static/index.html   聊天界面（工作流选择器/查询规划块/平台徽章/简报下载）
test_rbac.py        金标准评测（leak rate=0 + recall=100%）
test_web_matrix.py  Web 层 28 用例矩阵
sample_docs/        Meridian Pay 语料 8 篇（public/internal/gtm，虚构数据）
```
