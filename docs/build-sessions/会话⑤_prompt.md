# WorkBuddy 会话⑤ prompt（10/13 晚）

## 背景

延续会话④。当前状态（沙箱与本机一致，除注明外）：

1. `kb_qa.py` 已有 `AgentWorkflow` 注册表：`knowledge_qa` 与 `gtm_research`「账户情报」。gtm_research：plan_prompt 四维拆解（信号层判断 / ICP 分层与路由 / 套餐与折扣 / 客户案例与合规边界）、max_sub_questions=4、sub_queries_authoritative=True、offline_aspects 4 条、report_sections 4 组、human_decisions 5 条、synth_prompt 输出四节简报 +「需人工决策」清单。多跳骨架：规划 → 子问题各自检索过 RBAC → 合并去重 → 合成。
2. 语料 8 篇（public 2 / internal 2 / gtm 4：sales-playbook、pricing、case-studies、icp-targeting），Meridian Pay 语境。public 文档做过词面去重叠调优，**本次不动 public/internal 四篇**。
3. 品牌已定 Meridian Pay（合规徽章、平台徽章走 DOC_SOURCES 常量）。**本次不改 UI**：index.html 的下载简报.md 按钮、规划块、工作流选择器已就绪，简报新增的第五小节会自动包含在下载文件里。
4. 本机（落地后）差异：kb 层 min_score 双保险、authoritative 窗口 k*3、test_web_matrix 28/28、金标 test_rbac 8/8。

## 本次目标（账户情报升级，GitHub 竞品调研第三轮提炼，对齐 Aspire JD 加分项）

三项升级：

1. **信号评分卡**：信号层判断从「列信号」升级为 Fit×Timing×Access×Intent 四维打分（各 0-25、总分 100 → tier Hot/Warm/Cold/PARK + 响应 SLA），每个得分必须标注依据（用户提供事实 / 文档规则来源编号）。评分规则写进 icp-targeting.md（可检索、可被引用）。
2. **首触邮件草稿**：简报新增第五小节——主题行 + 3-4 句正文 + 送达合规注记（PDPA/CAN-SPAM），AI 味禁用词清单内置 prompt；邮件默认草稿态、发送需人工批准（human_decisions 加一条）。对齐 Aspire JD 加分项「邮件送达率 + GDPR/PDPA/CAN-SPAM」。
3. **anti-ICP disqualifiers + PARK**：icp-targeting.md 新增「不追清单」——红线直接不追（MAS 制裁主体/空壳/高风险行业/竞品长合约锁死）；PARK 层（预算未批/决策层换岗）季度重评；评分 <40 或命中红线 → 简报路由建议输出「不追 / PARK」而非硬推。

## 任务（两个改动）

### 改动 1：kb_qa.py 更新 gtm_research 的 synth_prompt / report_sections / human_decisions（plan_prompt 不动、max_sub_questions 保持 4）

synth_prompt 重写为（结构固定五节 + 决策清单）：

```
你是 GTM 账户情报助手。只依据下方来源资料与用户提供的公司事实, 输出一份「账户情报简报」, 结构固定为:
## 一、公司概况与信号评分卡
- 公司概况一句话; 然后 Fit×Timing×Access×Intent 四维各 0-25 分、总分 100, 映射 tier:
  80-100 Hot(AE 2 小时内响应) / 60-79 Warm(SDR 24 小时内首触) / 40-59 Cold(培育序列或 PARK) / <40 不追
- 每个得分必须标注依据: 「用户提供的事实」或「文档规则(来源编号)」; 用户未提供的事实维度写「资料未覆盖, 需人工补充」且该维记 0
- 命中「不追清单」红线时直接输出 tier=不追并说明命中的红线条款(来源编号)
## 二、ICP 分层判断与路由建议
- tier 驱动路由(自助/SDR/AE), 与评分卡联动; 命中红线时输出「不追 / PARK」而非硬推
## 三、推荐套餐与折扣权限说明
## 四、客户案例与合规边界
## 五、首触邮件草稿
- 主题行(≤8 个英文词) + 正文 3-4 句(钩子=评分卡里最强的 1 个信号 + 一句价值主张 + 一个行动邀请) + 合规注记一行
- 禁用 AI 味词: delve, leverage, elevate, unlock, seamless, cutting-edge, "I hope this email finds you well", "Let's schedule a call", 赋能, 一站式, 无缝, 极致, 闭环, 共创, 对齐(动词)
- 合规注记格式: 「合规注记: 基于公开融资/招聘信息首触(PDPA 允许); 含退订机制(CAN-SPAM); 邮件为草稿, 发送前需销售经理批准」
- 邮件素材只引用来源资料中的话术/案例(标注来源编号), 不编造客户信息
要求: 每小节末尾用括号标注引用的来源编号, 例如 (来源2); 资料未覆盖的内容写「资料未覆盖, 需人工补充」, 不要编造客户信息。
简报最后附一个「## 需人工决策」小节, 列出必须由人拍板的事项, 例如折扣是否特批、是否触发合规评估、是否需要 CFO 双签、首触邮件是否发送(默认草稿)——agent 完成分析, 决策权留给人。
```

- report_sections：第 1 组 title 改为「公司概况与信号评分卡」、keywords 加「评分 红线 不追」；新增第 5 组 `("首触邮件草稿", "邮件 话术 首触 主题行 合规")`（offline 回退时该节只输出检索到的素材片段 + 提示 LLM 不可用，不生成邮件正文）
- human_decisions 增加一条：`"首触邮件是否发送（默认草稿，销售经理批准后进入发送队列）？"`
- knowledge_qa、plan_prompt、offline_aspects 完全不动

### 改动 2：icp-targeting.md 重写（新增两节，既有内容保留原文）

在现有内容基础上**新增**两节（不要删改既有分层/融资信号段落——矩阵与金标依赖它们）：

1. **## 信号评分卡规则（Fit / Timing / Access / Intent）**：四维定义 + 分值锚点，例如：
   - Fit：行业/规模匹配 ICP 画像，沿用既有分层锚点定档（400 人以上=企业客户层满分档）
   - Timing：B 轮及以上融资=20-25 分；无融资但拓新市场=15-20 分；无信号=0-5 分
   - Access：官网自助入口/内推关系/高管 LinkedIn 可触达 各分值档
   - Intent：招聘增长/搜索意图/内容下载 各分值档
   - tier 映射与响应 SLA：Hot 80-100（AE 2h）/ Warm 60-79（SDR 24h）/ Cold 40-59（培育或 PARK）/ <40 不追
2. **## 不追清单（anti-ICP disqualifiers）与 PARK 层**：
   - 红线（直接不追）：MAS/OFAC 制裁名单主体；无实体经营的空壳主体；赌博/成人等高洗钱风险行业；与竞品签有 12 个月以上锁死合约
   - PARK（暂缓不追，季度重评）：预算未批 / 决策层换岗 / 组织重组中
   - 命中红线时，简报路由建议必须输出「不追」并引用本节

**⚠️ 语料调优铁律（同会话③）**：tokenize 是中文单字+bigram，跨目录同词会泄分。新增内容**避免使用「账户/结算/薪酬/报销/入职」等跨目录高频词**（internal/ops 有结算、internal/roadmap 有薪酬、public 有报销入职）；评分卡用词（Fit/Timing/Access/Intent/Hot/Warm/Cold/PARK/红线/不追）在 public/internal 文档中不应出现同词。**禁止在文档正文出现平台名（Confluence/Jira/Drive）**——平台标注只走代码常量 DOC_SOURCES。

## 硬约束

1. 只改 `kb_qa.py` 与 `icp-targeting.md`，**不输出 index.html / web_ui.py**（无 UI 改动需求）
2. 不破坏：knowledge_qa 行为、ask() 单跳、RBAC 子层级、min_score 门（沙箱为 web 层 _gate_sources）、审计逻辑、下载简报按钮
3. plan_prompt 不动、max_sub_questions=4 不动、offline_aspects 不动
4. public/internal 四篇文档不动；icp-targeting.md 只增不改（既有段落保留原文）
5. 品牌保持 Meridian Pay（本次不涉及 UI；若输出任何 HTML 片段需保持品牌与两个绿色合规徽章）

## 输出要求

1. `kb_qa.py` 完整代码（仅 gtm_research 的 synth_prompt / report_sections / human_decisions 变化）
2. `icp-targeting.md` 完整新版本
3. 两条 curl 验收命令：① workflow=gtm_research 客户调研（验证评分卡/邮件草稿）② knowledge_qa 简单问题（确认无回归）
4. 一句运行说明

## 验收标准

- gtm_research 问「一家 400 人、月均 300 笔跨境付款的外贸公司刚完成 B 轮融资、计划拓展东南亚，帮我做账户情报分析并给路由建议」→ 简报五节齐全：①评分卡四维+总分+tier（预期 Hot：B 轮融资=Timing 高分，得分标注来源编号）②路由=AE 直跟与评分卡联动 ③套餐折扣 ④案例话术 ⑤首触邮件草稿（主题行 ≤8 词、3-4 句、含合规注记、无禁用词）+ 需人工决策清单含「邮件发送批准」
- gtm_research 问命中红线的客户（如「一家没有实体经营的壳公司想做跨境收款」）→ 简报输出 tier=不追、命中红线条款带来源编号，路由建议不硬推
- knowledge_qa 行为与改造前一致（sub_queries/回答/来源不变）
- 本机落地后（铁律）：test_rbac 8/8 + test_web_matrix 28/28 双绿；矩阵用例若引用 icp-targeting 旧内容需同步更新；三角色被拦分 <0.24 留余量

## 若剩余时间（④⑤ 追加，各一处小改）

- ④ 异议处理：synth_prompt 第四小节加一句「案例话术中附 1 条该客户类型最常见异议及回应（LAARC：Listen/Acknowledge/Assess/Respond/Confirm，素材来自 case-studies 来源编号）」
- ⑤ 依据标注：synth_prompt 加一句「每小节结论标注依据类型（文档规则/用户事实/LLM 推断）」
