# 方案设计：GitHub Agent 生态排名调研

> **目标**: 产出可复用、可共享的调研方案与工具，用于生成"GitHub Agent 仓库排名"报告
> **场景**: 面向分享、文章、研究或内部汇报的数据快照生成工具
> **形态**: 独立命令行脚本（Python），配 Markdown 说明，任何人拿到即可跑

---

## 1. 目标 与 非目标

### ✅ 目标

1. **高覆盖率**：应上榜的 Agent 至少 90% 能抓到（不能漏 Cursor、Cline、Aider 这种"名字不含 agent"的项目）
2. **时效性**：需要引用数据时，前一天可重跑，保证结果接近最新
3. **可分类**：每条记录自动打标（产品/框架/中间件、形态、活跃度）
4. **可共享**：作为独立 Skill 交付，团队其他人拿到直接跑
5. **透明可信**：每一条数据可追溯到具体信源，方法论可解释

### ❌ 非目标（避免范围爆炸）

- ❌ 不追求"科研级完整性"——抓不到的项目就是抓不到
- ❌ 不做"哪个 Agent 最好"的主观排名——只呈现事实指标
- ❌ 不深度 review 代码质量——不读源代码
- ❌ 不自动生成幻灯片或长报告——只产出结构化数据与可读榜单
- ❌ 不实时监控——一次性跑批，不是长驻服务

---

## 2. 收录范围（边界定义）

### 两份榜单

```
┌──────────────────────────────────────────────────────────┐
│  主榜 · GitHub Agent 生态活跃 Top 50                     │
│    · 收录：产品型 Agent + 开发框架 + 中间件               │
│    · 过滤：pushed_at 在最近 6 个月内                     │
│    · 排序：star 数                                       │
│    · 用途：告诉听众"社区当前在关注什么"                   │
├──────────────────────────────────────────────────────────┤
│  副榜 · GitHub Agent 历史 Top 20（含已停维护）            │
│    · 与主榜同标准，但不过滤 pushed_at                    │
│    · 标注活跃度（活跃 / 半活跃 / 停维护）                 │
│    · 用途：讲历史脉络（Auto-GPT 等放这里）                │
└──────────────────────────────────────────────────────────┘
```

### 收录标准（主榜和副榜 1）

**符合以下 3 类之一**：
1. **产品型 Agent**：终端用户能装/能用（Cursor、Cline、Aider、OpenCode）
2. **Agent 开发框架**：LangChain、LangGraph、AutoGen、CrewAI
3. **Agent 中间件与子组件**：关键 MCP servers、评测工具、安全扫描器

### 明确排除

- ❌ Awesome 列表、papers 列表（是"目录"，不是 Agent）
- ❌ 纯模型运行时（Ollama、vLLM、SGLang）
- ❌ 通用 LLM 应用（除非核心能力是 Agent）
- ❌ 已归档 / 已删除的仓库
- ❌ Fork（除非 fork 已成为主流分支）

---

## 3. 九大难点的解决方案

### 难点 1 · "什么算 Agent" 边界模糊 → **规则明确 + 人在环兜底**

**方案**：
- **收录规则**已在第 2 节明确写死
- **执行时**先由自动规则打标签，边界模糊的项目**打 `?` 标记**由人审核
- 每次跑完后有一个 "review 队列"，人工判定 5-10 条边界 case

**具体规则示例**：
```
IF repo.name matches /awesome-|-list$|papers-|-papers$/ THEN 排除
IF repo.description contains "model runtime|inference server" THEN 排除
IF repo.description contains "agent|copilot|coding assistant|autonomous" THEN 候选
IF repo in known_agent_seed_list THEN 强制收录
```

### 难点 2 · Star 数不等于价值 → **多维度呈现，不做主观排名**

**方案**：
- 不给"综合分"，只呈现原始事实：`stars, forks, pushed_at, license, language, 类型`
- 让读者/听众自己判断
- **额外维度**：加一列 `stars_last_90_days`（近 90 天新增 star），反映增长趋势
  - 数据源：从 GitHub API 拿不到直接的"近期 star 变化"，可用 `star-history` 类接口或者第三方（这里暴露一个 gap）

### 难点 3 · GitHub Search API 限制 → **多关键词组合 + 多接口交叉**

**方案**：**并行跑多组关键词搜索，去重合并**。

关键词种子集（`keywords.json`）：
```json
[
  "agent",
  "ai-agent", "llm-agent", "autonomous-agent",
  "coding-agent", "coding-assistant",
  "copilot",
  "agentic",
  "chatbot-framework",
  "multi-agent",
  "browser-agent",
  "code-review-agent"
]
```

对每个关键词：
- 调用 `GET /search/repositories?q={kw}&sort=stars&order=desc&per_page=100`
- 分页取前 300-500 条
- 收集到一个总池

**跨关键词去重**：按 `owner/name` 唯一化

### 难点 4 · "名字不含 agent" 的 Agent 漏检 → **种子清单 + Awesome 列表交叉**

**方案**：
1. **维护 seed list**（`seed-repos.json`）：手动整理已知的 60+ 个知名 Agent 仓库（Cursor、Cline、Aider、OpenCode、Continue……）——**强制收录**，绕过关键词搜索
2. **爬 Awesome 列表**：以下几个高质量列表交叉：
   - `e2b-dev/awesome-ai-agents`
   - `hyp1231/awesome-llm-powered-agent`
   - `github.com/kyrolabs/awesome-agents`
   - `github.com/slavakurilyak/awesome-ai-agents`
3. 从 Awesome 列表的 Markdown 里正则抽取所有 `github.com/xxx/yyy` 链接
4. 对每个链接调用 `GET /repos/{owner}/{name}` 拿元数据

### 难点 5 · Awesome 列表本身要不要入榜 → **收录 Awesome 列表内的项目，Awesome 本身不入榜**

**方案**：
- Awesome 列表**作为发现工具**，不作为榜单成员
- 在自动规则里：`name matches /awesome-|-list$/i` 直接排除
- Awesome 列表读进来 → 抽取仓库 URL → 抽取的仓库进入候选池

### 难点 6 · 去重 & 归一化 → **owner/name 强制唯一 + 团队合并可选**

**方案**：
- **主键**：`{owner}/{name}` 小写、trim
- **改名处理**：GitHub API 会自动 302 到新名字，通过 `full_name` 拿最新名
- **Fork 处理**：`fork=true` 的默认排除；除非它已经比 upstream 更活跃（`stars > upstream.stars * 2`）
- **团队合并**：**不合并**（langchain-ai/langchain 和 langchain-ai/langgraph 分别入榜），但输出里加 `owner` 列方便人肉聚合

### 难点 7 · 元数据深度不足 → **两级抓取 + 类型自动打标**

**方案**：

**Level 1（快）**：GitHub Search API → 拿到 name / stars / forks / language / description / pushed_at / license

**Level 2（慢）**：对 Level 1 结果的 Top N 项目，读 README：
- `GET /repos/{owner}/{name}/readme`
- Base64 decode → 抽取前 500 字
- **自动打标**（基于关键词）：
  - `CLI` → README 提到 `install ... command line / terminal / cli`
  - `IDE-plugin` → 提到 `vscode / extension / plugin`
  - `AI-IDE` → 提到 `editor / IDE / fork of VSCode`
  - `Framework` → 提到 `framework / library / build your own`
  - `Multi-Agent` → 提到 `multi-agent / crew / team`
  - `MCP` → 提到 `MCP / model context protocol`
- 打不到标签的项目 → `?`（进人审队列）

### 难点 8 · Manus 类工具做不好这件事 → **本方案针对性对症**

对比通用 Agent 的失败模式：

| 通用 Agent 的问题 | 本方案的对策                       |
| ----------------- | ---------------------------------- |
| 训练数据陈旧      | 直接调 GitHub API 拿实时数据       |
| 不做多信源交叉    | 15 个关键词 + 6 个 Awesome 列表交叉 |
| 无 seed list      | 维护 60+ 项 seed，绕过 name 关键词 |
| 边界不清一锅乱炖  | 三份榜单分开，明确排除规则         |
| 无法人审兜底      | `?` 标签队列人工 review            |

### 难点 9 · Star 数每天在变 → **执行时打时间戳，声明数据快照日期**

**方案**：
- 每次执行在输出里记录 `snapshot_date = ISO 8601`
- Markdown 输出顶部醒目标注："本报告数据快照于 2026-07-14 15:30 UTC"
- 在引用到分享材料、文章或报告时，必须同步注明日期

---

## 4. 执行流程

```
┌─────────────────────────────────────────────────────────┐
│  Step 1 · 加载配置                                       │
│    · keywords.json（关键词种子）                         │
│    · seed-repos.json（强制收录清单）                     │
│    · awesome-lists.json（Awesome 列表 URL）             │
├─────────────────────────────────────────────────────────┤
│  Step 2 · 多路发现（并行）                                │
│    · 路径 A：关键词搜索 GitHub Search API                │
│    · 路径 B：读取 seed list 直接拿仓库                    │
│    · 路径 C：抓 Awesome 列表 → 抽取 repo URL             │
│    · 合并去重 → 得到候选池（预计 200-500 个）            │
├─────────────────────────────────────────────────────────┤
│  Step 3 · 过滤                                          │
│    · 排除 Awesome / papers / model runtime               │
│    · 排除 archived / fork（除非高于 upstream）            │
│    · 排除描述不含 Agent 相关词的（除非在 seed）           │
├─────────────────────────────────────────────────────────┤
│  Step 4 · 元数据补全                                     │
│    · 对每个候选拿完整 repo 元数据                         │
│    · 对 Top 60 项目额外读 README（用于打标签）           │
├─────────────────────────────────────────────────────────┤
│  Step 5 · 类型分类打标                                   │
│    · 自动打标签（类型 / 形态）                            │
│    · 无法自动判定的进 `?` 队列                            │
├─────────────────────────────────────────────────────────┤
│  Step 6 · 生成榜单                                       │
│    · 按 pushed_at 分主榜 / 历史榜                        │
│    · 按 star 排序取 Top 50 / Top 20                     │
├─────────────────────────────────────────────────────────┤
│  Step 7 · 输出                                          │
│    · JSON（结构化，供后续处理）                          │
│    · Markdown（人类可读，适合直接引用到文档）             │
│    · CSV（Excel 分析用）                                 │
│    · review-queue-YYYY-MM-DD.md（人审队列，边界 case）              │
└─────────────────────────────────────────────────────────┘
```

**预估耗时**：单次跑通约 2-5 分钟（有 token） / 30-60 分钟（无 token，限流严重）

---

## 5. 输出数据结构

### 每条记录的字段

```json
{
  "rank": 1,
  "name": "cline",
  "owner": "cline",
  "full_name": "cline/cline",
  "url": "https://github.com/cline/cline",
  "description": "Autonomous coding agent right in your IDE, capable of creating/editing files, executing commands, using the browser, and more with your permission every step of the way.",
  "stars": 42800,
  "forks": 5820,
  "language": "TypeScript",
  "license": "Apache-2.0",
  "created_at": "2024-07-06T12:34:00Z",
  "pushed_at": "2026-07-13T20:15:00Z",
  "is_active": true,
  "type": "Product",
  "form": "IDE-plugin",
  "auto_tags": ["ide-plugin", "vscode", "coding-agent"],
  "discovery_source": ["keyword:coding-agent", "awesome:e2b-dev/awesome-ai-agents", "seed-list"],
  "review_status": "auto"
}
```

### 输出文件

```
output/
├── snapshot-YYYY-MM-DD.json           # 完整数据
├── ranking-active-top50-YYYY-MM-DD.md            # 主榜 · 人类可读
├── ranking-legacy-top20-YYYY-MM-DD.md            # 历史榜 · 人类可读
├── ranking-all-YYYY-MM-DD.csv                    # Excel 用
├── review-queue-YYYY-MM-DD.md                    # 需人审的边界 case
└── metadata-YYYY-MM-DD.json                      # 快照日期、跑批参数、总耗时
```

---

## 6. 已知局限与偏差

**这些是无法在本方案内完全解决的问题，公开文档或分享材料里都应该主动声明。**

### 6.1 数据源局限
- **只覆盖 GitHub**：GitLab、Gitee、私有仓库不在范围
- **只用公开 API**：GitHub 的"trending"内部算法闭源，无法完全复现

### 6.2 时间性局限
- **快照式数据**：不呈现 star 增长速度（除非跑两次 diff）
- **pushed_at 不等于活跃度**：有些项目一个月才发一次 release，看 pushed_at 不准

### 6.3 分类局限
- **自动打标**基于 README 关键词，可能误判（如某项目说"we are NOT a framework"会被误标为 Framework）
- **类型边界模糊的项目**（如 LlamaIndex 既是框架又有 Agent 功能）标签只能选一个主要的

### 6.4 覆盖率的实际估计
- **产品型 Agent**：预计 90%+ 覆盖率（seed list 兜底）
- **框架**：预计 85%（大多数框架名字含 langchain / crew / autogen 等）
- **中间件**：预计 70%（长尾多、命名分散）
- **总体**：预计 80-85%

---

## 7. 与分享材料的对接

### 适合怎样展示

如果你要把结果放进分享材料、文章或周报，可以按下面的结构组织：
- 一页或一节展示活跃榜前 10-20
- 一页或一节展示历史榜，补充生态演进脉络

### 呈现方式建议

- 主榜 Top 50 → **不建议全展示**，只展示 Top 20 或 Top 15
- 视觉建议：横向条形图（star 数）+ 颜色标类型
- 数据快照日期放在醒目位置

---

## 8. 交付物清单

- [x] 本设计文档
- [ ] `skills/agent-repo-ranking/SKILL.md`（使用说明）
- [ ] `skills/agent-repo-ranking/search_agents.py`（主脚本）
- [ ] `skills/agent-repo-ranking/data/keywords.json`
- [ ] `skills/agent-repo-ranking/data/seed-repos.json`
- [ ] `skills/agent-repo-ranking/data/awesome-lists.json`
- [ ] `skills/agent-repo-ranking/data/exclude-patterns.json`
- [ ] 提供一次真实跑通的完整输出样例

---

## 9. 后续可能的扩展

**不在本次范围，但值得记录**：

- 每周自动跑 + 生成变化日报
- 加入 star 增长速度 / trending 信号
- 加入 GitHub Discussions / Issues 活跃度指标
- 加入社交媒体声量（Reddit、Twitter、HN 提及）
- 支持导出为图表或幻灯片友好的格式
- 支持中文社区专项（Gitee 榜）

这些扩展不会破坏本次的 Skill 架构，可以增量添加。
