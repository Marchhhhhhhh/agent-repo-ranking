# Agent Repo Ranking — 流程图

> 本文档使用 Mermaid 语法绘制，GitHub 原生渲染。共 6 张图，覆盖从启动到输出的完整流程。

---

## 1. 全局流程总览

```mermaid
flowchart TD
    START([python3 search_agents.py]) --> LOAD[加载配置文件<br/>keywords / seed / awesome / exclude / type-overrides]
    LOAD --> TOKEN{检测 GITHUB_TOKEN?}
    TOKEN -->|有 Token| GH_OK[初始化 GitHubClient<br/>search: 30/min, core: 5000/h]
    TOKEN -->|无 Token| GH_WARN[降级模式<br/>search: 10/min, core: 60/h<br/>⚠️ 可能跑不完]
    GH_OK --> DISCOVER
    GH_WARN --> DISCOVER

    subgraph DISCOVER [Phase 1 · 三路发现]
        direction TB
        A[路径 A · 关键词搜索<br/>15 个关键词 × 每词 300 条]
        B[路径 B · Seed list<br/>60+ 已知仓库强制收录]
        C[路径 C · Awesome 列表<br/>6 个列表 README 抽取 URL]
    end

    DISCOVER --> MERGE[合并去重<br/>记录 discovery_source]
    MERGE --> FILTER

    subgraph FILTER [Phase 2 · 过滤排除]
        direction TB
        F1{在 Seed list 中?}
        F1 -->|是| KEEP1[强制保留]
        F1 -->|否| F2{命中 hard_exclude?<br/>名称模式 / 描述关键词 / 全名黑名单}
        F2 -->|是| EXCLUDE[排除]
        F2 -->|否| F3{stars < 100?}
        F3 -->|是| EXCLUDE
        F3 -->|否| F4{archived?}
        F4 -->|是| EXCLUDE
        F4 -->|否| F5{fork?}
        F5 -->|是| EXCLUDE
        F5 -->|否| F6{命中 soft_exclude?<br/>描述含不相关关键词}
        F6 -->|是| EXCLUDE
        F6 -->|否| F7{有 Agent 信号?<br/>topics / 名称 / 描述}
        F7 -->|否| EXCLUDE
        F7 -->|是| KEEP2[保留]
    end

    FILTER --> NORM[Phase 3 · 归一化<br/>raw dict → Repo 对象]
    NORM --> CLASSIFY

    subgraph CLASSIFY [Phase 3 · 分类打标]
        direction TB
        C1{命中 type-overrides.json?}
        C1 -->|是| C1A[使用人工/AI 确认的类型]
        C1 -->|否| C2{topics 匹配<br/>TOPIC_TYPE_MAP?}
        C2 -->|命中| C2A[使用 topics 映射类型]
        C2 -->|未命中| C3[description 关键词规则<br/>TYPE_RULES 匹配]
        C3 --> C3A{匹配到?}
        C3A -->|是| C3B[使用规则类型]
        C3A -->|否| C4[type = ? 需人审]
    end

    CLASSIFY --> DESC[生成一句话描述<br/>CN_DESC_OVERRIDES → 模板化 → 英文原文]
    DESC --> OUTPUT

    subgraph OUTPUT [Phase 4 · 输出]
        direction LR
        O1[活跃榜 Top N<br/>Active .md]
        O2[历史榜 Top N<br/>Legacy .md]
        O3[产品榜 Top N<br/>Products .md]
        O4[snapshot.json]
        O5[metadata.json]
        O6[ranking-all.csv]
        O7[review-queue.md]
    end

    OUTPUT --> DONE([✅ 完成])
```

---

## 2. Phase 1 · 三路发现机制

```mermaid
flowchart LR
    subgraph PathA [路径 A · 关键词搜索]
        direction TB
        A1[读取 keywords.json<br/>15 个关键词] --> A2[逐个调用<br/>GitHub Search API]
        A2 --> A3[每词取前 300 条<br/>分页 100/页]
        A3 --> A4[去重合并<br/>记录 source: keyword:xxx]
    end

    subgraph PathB [路径 B · Seed List]
        direction TB
        B1[读取 seed-repos.json<br/>60+ 已知仓库] --> B2[逐个调用<br/>GET /repos/{owner}/{name}]
        B2 --> B3[获取完整元数据<br/>记录 source: seed-list]
    end

    subgraph PathC [路径 C · Awesome 列表]
        direction TB
        C1[读取 awesome-lists.json<br/>6 个列表源] --> C2[逐个获取<br/>README 原始内容]
        C2 --> C3[正则提取<br/>github.com/owner/name]
        C3 --> C4[过滤非仓库路径<br/>features / sponsors / orgs ...]
        C4 --> C5[新 URL 补拉元数据<br/>已有则仅追加 source]
    end

    PathA --> MERGE[合并候选池<br/>dict full_name → raw data]
    PathB --> MERGE
    PathC --> MERGE

    MERGE --> POOL[候选池<br/>200~500 个仓库]
```

---

## 3. Phase 2 · 过滤排除决策树

```mermaid
flowchart TD
    IN[候选仓库] --> S1{full_name 在<br/>seed list 中?}
    S1 -->|是 ✅| PASS[保留]
    S1 -->|否| S2{full_name 在<br/>hard_exclude_full_names 中?}

    S2 -->|是 ❌| DROP[排除: hard_exclude:full_name]
    S2 -->|否| S3{name 匹配<br/>hard_exclude_name_patterns?}

    S3 -->|是 ❌| DROP2[排除: hard_exclude:name]
    S3 -->|否| S4{description 含<br/>hard_exclude 关键词?}

    S4 -->|是 ❌| DROP3[排除: hard_exclude:desc]
    S4 -->|否| S5{stargazers_count<br/>< 100?}

    S5 -->|是 ❌| DROP4[排除: min_stars]
    S5 -->|否| S6{archived == true?}

    S6 -->|是 ❌| DROP5[排除: archived]
    S6 -->|否| S7{fork == true?}

    S7 -->|是 ❌| DROP6[排除: fork]
    S7 -->|否| S8{description 含<br/>soft_exclude 关键词?}

    S8 -->|是 ❌| DROP7[排除: soft_exclude:desc]
    S8 -->|否| S9{require_agent_signal<br/>== true?}

    S9 -->|否| PASS2[保留]
    S9 -->|是| S10{topics / name / desc<br/>含 Agent 信号?}

    S10 -->|是 ✅| PASS3[保留]
    S10 -->|否 ❌| DROP8[排除: no_agent_signal]

    style PASS fill:#d4edda,stroke:#28a745
    style PASS2 fill:#d4edda,stroke:#28a745
    style PASS3 fill:#d4edda,stroke:#28a745
    style DROP fill:#f8d7da,stroke:#dc3545
    style DROP2 fill:#f8d7da,stroke:#dc3545
    style DROP3 fill:#f8d7da,stroke:#dc3545
    style DROP4 fill:#f8d7da,stroke:#dc3545
    style DROP5 fill:#f8d7da,stroke:#dc3545
    style DROP6 fill:#f8d7da,stroke:#dc3545
    style DROP7 fill:#f8d7da,stroke:#dc3545
    style DROP8 fill:#f8d7da,stroke:#dc3545
```

---

## 4. Phase 3 · 分类打标优先级

```mermaid
flowchart TD
    REPO[Repo 对象<br/>已归一化] --> P1

    subgraph P1 [优先级 1 · type-overrides.json]
        direction TB
        P1A[查 full_name.lower] --> P1B{在 overrides 中?}
        P1B -->|是| P1C[直接使用 type + form<br/>review_status = manual]
        P1B -->|否| P2
    end

    subgraph P2 [优先级 2 · Topics 映射]
        direction TB
        P2A[遍历 repo.topics] --> P2B{topic 在<br/>TOPIC_TYPE_MAP 中?}
        P2B -->|是| P2C[映射 type<br/>如 mcp → Middleware]
        P2B -->|否| P3
        P2C --> P2D{topic 在<br/>TOPIC_FORM_MAP 中?}
        P2D -->|是| P2E[映射 form<br/>如 mcp → MCP-Ecosystem]
        P2D -->|否| P3
    end

    subgraph P3 [优先级 3 · Description 规则]
        direction TB
        P3A[description.toLowerCase] --> P3B{匹配 TYPE_RULES?}
        P3B -->|Framework| P3C["framework for / agent framework<br/>multi-agent framework / sdk for ..."]
        P3B -->|Middleware| P3D["mcp server / evaluation<br/>observability / gateway ..."]
        P3B -->|Product| P3E["install / cli tool / coding agent<br/>ai assistant / desktop app ..."]
        P3B -->|未命中| P3F[type = ?]
        P3C --> P3G[匹配 FORM_RULES<br/>IDE-plugin / CLI / AI-IDE ...]
        P3D --> P3G
        P3E --> P3G
        P3F --> P3H[review_status = needs_review]
    end

    P1C --> TAGS
    P2E --> TAGS
    P2D -.->|未命中 form| P3
    P3G --> TAGS
    P3H --> TAGS

    TAGS[附加 auto_tags<br/>coding / browser / cn / lang:xxx]
    TAGS --> DESC[生成一句话描述]

    subgraph DESC [描述生成优先级]
        direction TB
        D1{在 CN_DESC_OVERRIDES 中?<br/>45 个知名项目}
        D1 -->|是| D1A[使用精准中文描述]
        D1 -->|否| D2{原描述含中文?}
        D2 -->|是| D2A[直接使用原描述]
        D2 -->|否| D3{匹配特征模板?}
        D3 -->|是| D3A["模板化中文（如「编码 Agent 相关项目」）<br/>+ 英文原文"]
        D3 -->|否| D4[保留英文原文]
    end

    style P1C fill:#d4edda,stroke:#28a745
    style P1F fill:#fff3cd,stroke:#ffc107
    style P3F fill:#fff3cd,stroke:#ffc107
    style P3H fill:#f8d7da,stroke:#dc3545
```

---

## 5. Phase 4 · 输出文件生成

```mermaid
flowchart LR
    REPOS[全部 Repo 列表<br/>已分类 + 已描述]

    REPOS --> SPLIT{按活跃度分流}
    SPLIT -->|days_since_push ≤ 180| ACTIVE[活跃仓库]
    SPLIT -->|days_since_push > 180| LEGACY[历史仓库]

    ACTIVE --> SORT_A[按 stars 降序]
    SORT_A --> TOP_A[取 Top N<br/>默认 50]
    TOP_A --> MD_A[active-ranking-*.md<br/>Markdown 表格 + 一句话描述]

    LEGACY --> SORT_L[按 stars 降序]
    SORT_L --> TOP_L[取 Top N<br/>默认 20]
    TOP_L --> MD_L[legacy-ranking-*.md<br/>含已停维护项目]

    REPOS --> FILTER_P[type == Product]
    FILTER_P --> SORT_P[按 stars 降序]
    SORT_P --> TOP_P[取 Top N<br/>默认 50]
    TOP_P --> MD_P[products-ranking-*.md<br/>仅终端产品]

    REPOS --> ALL[全部保留仓库]
    ALL --> JSON[snapshot-*.json<br/>完整数据快照<br/>含 excluded 列表]
    ALL --> META[metadata-*.json<br/>运行参数 + 文件清单]
    ALL --> CSV[ranking-all-*.csv<br/>18 列完整数据]
    ALL --> REVIEW[review-queue-*.md<br/>type=? 的人审队列]

    style MD_A fill:#cce5ff,stroke:#007bff
    style MD_L fill:#d4edda,stroke:#28a745
    style MD_P fill:#f8d7da,stroke:#dc3545
    style JSON fill:#fff3cd,stroke:#ffc107
    style CSV fill:#e2e3e5,stroke:#6c757d
    style REVIEW fill:#f8d7da,stroke:#dc3545
```

---

## 6. 数据流转：配置文件 → 处理 → 产物

```mermaid
flowchart TD
    subgraph CONFIG [配置文件 / data/]
        direction LR
        C1[keywords.json<br/>15 个搜索词]
        C2[seed-repos.json<br/>60+ 已知仓库]
        C3[awesome-lists.json<br/>6 个列表源]
        C4[exclude-patterns.json<br/>排除规则 + Agent 信号]
        C5[type-overrides.json<br/>AI/人工确认映射]
    end

    subgraph CODE [search_agents.py · 1370 行]
        direction TB
        M1[GitHubClient<br/>双限流桶 + 代理支持]
        M2[discover_*<br/>三路发现]
        M3[should_exclude<br/>过滤决策]
        M4[normalize<br/>raw → Repo]
        M5[classify<br/>三级优先打标]
        M6[make_cn/en_desc<br/>一句话描述]
        M7[output_*<br/>多格式输出]
    end

    subgraph ENV [环境]
        E1[.env<br/>GITHUB_TOKEN=xxx]
        E2[http_proxy / https_proxy<br/>可选代理]
    end

    subgraph OUTPUT [产物 / output/]
        direction LR
        O1[active-ranking-*.md]
        O2[legacy-ranking-*.md]
        O3[products-ranking-*.md]
        O4[snapshot-*.json]
        O5[metadata-*.json]
        O6[ranking-all-*.csv]
        O7[review-queue-*.md]
    end

    E1 --> M1
    E2 --> M1
    C1 --> M2
    C2 --> M2
    C3 --> M2
    C4 --> M3
    C5 --> M5

    M1 --> M2
    M2 --> M3
    M3 --> M4
    M4 --> M5
    M5 --> M6
    M6 --> M7

    M7 --> O1
    M7 --> O2
    M7 --> O3
    M7 --> O4
    M7 --> O5
    M7 --> O6
    M7 --> O7
```

---

## 7. AI 自动审查流程（取代人审）

> `search_agents.py` 保持零依赖不动。跑批后由 AI agent 读取 review-queue，
> 分类后写回 `type-overrides.json`（收录）和 `exclude-patterns.json`（排除），重跑即生效。

```mermaid
flowchart TD
    RUN[python3 search_agents.py] --> OUT[产出 7 个文件<br/>含 review-queue.md]
    OUT --> QUEUE{review-queue<br/>有 type=? 项?}
    QUEUE -->|无| DONE([✅ 完成，榜单可直接使用])
    QUEUE -->|有| AI[AI agent 审查<br/>读 description + topics<br/>必要时联网搜索]

    AI --> DECIDE{逐个判定}
    DECIDE -->|是 Agent 项目| CLASSIFY[确定 type + form]
    DECIDE -->|非 Agent 项目| EXCLUDE[标记排除]

    CLASSIFY --> WRITE_TO[写入 type-overrides.json]
    EXCLUDE --> WRITE_EX[写入 exclude-patterns.json<br/>hard_exclude_full_names]

    WRITE_TO --> RERUN[重跑 search_agents.py]
    WRITE_EX --> RERUN
    RERUN --> QUEUE

    style DONE fill:#d4edda,stroke:#28a745
    style AI fill:#cce5ff,stroke:#007bff
    style EXCLUDE fill:#f8d7da,stroke:#dc3545
```

### 审查判定规则

| 信号 | 判定方向 | 示例 |
|---|---|---|
| description 含 framework / sdk / library | → Framework | "A programming framework for agentic AI" |
| description 含 install / app / assistant / agent | → Product | "AI pair programming in your terminal" |
| description 含 mcp / memory / observability / sandbox | → Middleware | "Universal memory layer for AI Agents" |
| topics 含 multi-agent | form = Multi-Agent-Framework | crewAI, AutoGen |
| topics 含 vscode-extension / cli | form = IDE-plugin / CLI | cline, aider |
| 仓库本身是教程 / 指南 / 泄露集 | → 排除 | claude-code-best-practice, system_prompts_leaks |
| 仓库不是 Agent 生态项目 | → 排除 | tldraw (canvas SDK), huginn (web monitor) |

### 迭代特性

首次跑批后 review-queue 可能有 30-50 项；AI 审查写入 overrides 后重跑，
queue 通常降到 0-5 项（新发现的仓库）。随着 overrides 积累，后续跑批的
queue 会越来越少，趋近于只有真正新出现的仓库。

---

## 附：关键参数速查

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--top` | 50 | 活跃榜 / 产品榜取多少项 |
| `--legacy` | 20 | 历史榜取多少项 |
| `--lang` | bilingual | 输出语言：zh / en / bilingual |
| `--output-dir` | `./output/` | 输出目录 |
| `--data-dir` | `./data/` | 配置文件目录 |
| `--quiet` | false | 减少日志输出 |
| `MAX_PER_KEYWORD` | 300 | 每个关键词最多抓多少条 |
| `ACTIVE_DAYS_THRESHOLD` | 180 | 活跃 / 历史分界线（天） |
| `min_stars` | 100 | 最低 star 门槛 |
| Search API 限流 | 30/min (有 Token) | 10/min (无 Token) |
| Core API 限流 | 5000/hour (有 Token) | 60/hour (无 Token) |
