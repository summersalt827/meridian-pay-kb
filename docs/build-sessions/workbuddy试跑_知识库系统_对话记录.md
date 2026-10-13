# WorkBuddy 试跑记录：最小知识库问答系统（9/26）

> 提交物底稿之一（对话历史）。代码已落地 `kb_qa/` 目录并本机验证通过（9/9 测试 + RBAC 端到端）。
> 生成工具：WorkBuddy（hyscandy@163.com 账号）

## 使用的 Prompt

```
帮我写一个最小知识库问答系统：
1. ingest Markdown 文档并分块
2. 关键词/向量检索
3. 用 LLM 生成回答
4. 带简单 RBAC：admin 可看全部，viewer 只能看指定目录
5. 每次问答写入审计日志（时间/用户/问题/来源文档）
用 Python 实现，尽量少依赖。
```

## 生成过程记录

- 我来帮你实现这个最小知识库问答系统。先看一下工作目录，然后直接动手写代码。
- 工作目录干净，numpy 和 requests 都可用。我直接开始构建。这是一个完整的、多模块的系统，我会用一个清晰的主模块 + 示例文档 + 测试 + 说明文档来实现。
- `_now_iso` 写得有点绕，修一下，让它干净点。同时创建示例文档和测试。
- 现在创建一套能体现 RBAC 的示例文档（public 目录 viewer 可见，internal 目录仅 admin 可见）。
- 现在写测试，覆盖分块、RBAC、路径逃逸、检索、审计日志、抽取式回答等。
- 跑一下测试，验证整个系统是否正常工作。
- 全部通过。再用命令行对示例文档跑几个真实场景，演示 RBAC 和审计日志的端到端行为。
- 全部场景表现正确：admin 能查到内部机密、viewer 被拦截、viewer 能查到 public 内容、审计日志完整记录三元组。
- 最后做个小优化：把 `requests` 改成延迟导入，这样纯离线使用时硬依赖只剩 `numpy`，更贴合"尽量少依赖"。
- 全部 9 个测试通过，离线硬依赖仅剩 `numpy`。系统已完整可用，下面把成果交付给你。
- 已交付。下面是这套系统的完整说明。

## 成果文件

| 文件 | 作用 |
|------|------|
| `kb_qa.py` | 主系统，约 400 行，单文件涵盖全部 5 个需求 |
| `test_kb.py` | 9 个离线单测，全部通过 |
| `sample_docs/public/*.md` | viewer 可访问的示例文档 |
| `sample_docs/internal/*.md` | 仅 admin 可见的示例文档 |

## 5 个需求的实现方式

1. **摄入 Markdown 并分块** — `MarkdownIngestor` 递归读取 `.md`，按标题层级切节（维护标题栈），节内按段落打包到目标字符数（默认 800，重叠 120），超长段落硬切。每个 `Chunk` 带标题链、相对路径、正文。
2. **关键词/向量检索** — 双引擎 + 混合：
   - `BM25Index`：经典 BM25（k1=1.5, b=0.75）做关键词检索；
   - `TfidfEmbedder`：TF-IDF 向量空间模型（smooth idf + L2 归一 + 余弦相似）做向量检索，离线零额外依赖；
   - `Retriever` 用 `alpha` 权重融合两路归一化分数（`alpha=0` 纯关键词，`1` 纯向量，默认 0.5）。中英文混合分词：英文按词、中文单字 + 相邻 bigram。
3. **LLM 生成回答** — `Generator` 调 OpenAI 兼容 `/chat/completions`，带"只据来源回答、禁止编造、标注来源编号"的提示。**未配置 API Key 时自动回退抽取式回答**（返回最相关分块摘要 + 引用），保证开箱即用。
4. **简单 RBAC** — `User.can_access()` 判定：`admin` 全通过；`viewer` 仅当 `path` 落在 `allowed_dirs` 之下才放行。路径先 `normpath` 归一化，**显式防 `../` 逃逸**（单测覆盖）。
5. **审计日志** — `AuditLog` 用 SQLite（`kb_audit.db`），每次问答写入：时间(`ts`) / 用户 / 角色 / 问题 / 来源文档(JSON, 含路径·标题·分数·doc_id) / 回答 / 模式。`recent()` 可查最近记录。

## 用法示例

```bash
# admin 问（能命中 internal 机密）
python3.11 kb_qa.py "公司薪酬带宽是多少?" --user alice --role admin

# viewer 问同一问题（被 RBAC 拦截 → no_context）
python3.11 kb_qa.py "公司薪酬带宽是多少?" --user bob --role viewer --dirs public

# viewer 问 public 内容（命中）
python3.11 kb_qa.py "如何配置 RBAC 权限?" --user bob --role viewer --dirs public

# 查看审计日志
python3.11 kb_qa.py
```

## 接入真实 LLM（可选）

```bash
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="sk-..."
export LLM_MODEL="gpt-4o-mini"
# 可选：真实稠密向量（替代 TF-IDF）
export USE_DENSE_EMBED=1
export EMBED_BASE_URL="https://api.openai.com/v1"
export EMBED_API_KEY="sk-..."
```

## 设计取舍

- **硬依赖仅 `numpy`**：`requests` 改为延迟导入，纯离线使用时不需要。向量用 TF-IDF 稀疏模型（本身是合法的向量检索），可选切到稠密 `OpenAIEmbedder`。
- **单文件主模块**：贴合"最小"，但内部按清晰分节（数据结构/分词/摄入/向量化/BM25/检索/生成/审计/门面）。
- 实测端到端：admin 命中 `internal/roadmap.md` 薪酬信息，viewer 同问被拦回退 `no_context`，viewer 问 public 内容正确命中 `public/faq.md`，审计日志三元组（时间/用户/问题/来源）完整可查。

## 截图

- [x] 4 张已存 `证据/workbuddy截图_01~04_2026-09-26.png`（9/26 晚通过微信传入并归档）
