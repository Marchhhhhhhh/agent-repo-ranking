---
name: agent-repo-ranking
description: 生成 GitHub Agent 仓库排名调研报告 / Generate GitHub agent ecosystem ranking reports
allowed-tools: Bash, Read
---

# GitHub Agent 仓库排名调研 / GitHub Agent Ecosystem Ranking

一个用于生成 "GitHub Agent 仓库排名报告" 的 Skill。  
A skill for generating ranked snapshots of the GitHub agent ecosystem.

产出两份榜单：  
It produces two lists:

- **主榜**：GitHub Agent 生态活跃 Top 50（近 6 个月有提交）
- **副榜**：GitHub Agent 历史 Top 20（含已停维护，如 Auto-GPT）

## 何时使用这个 Skill / When To Use This Skill

当用户要求你：  
Use this skill when the user wants to:
- 做 Agent 领域的 GitHub 项目调研
- 找出目前 star 最高的 Agent 相关仓库
- 更新此前的 Agent 排名报告
- 分析 Agent 生态现状

## 快速开始

### 前置条件 / Prerequisites

推荐设置 GitHub Personal Access Token（否则限流严重）。  
A GitHub Personal Access Token is strongly recommended because of rate limits.

有三种方式：  
Three common ways:

**方式 A · `.env` 文件（推荐）**

```bash
cd agent-repo-ranking   # 进入仓库根目录 / enter the repo root
cp .env.example .env
# 编辑 .env，把 ghp_xxx 换成你自己的 token
```

`.env` 已在 `.gitignore` 中忽略，不会误提交。

**方式 B · 临时环境变量（一次性使用）**

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxx
```

**方式 C · 永久写到 shell rc**

```bash
echo 'export GITHUB_TOKEN=ghp_xxx' >> ~/.zshrc
source ~/.zshrc
```

Token 创建：<https://github.com/settings/tokens>
只需勾选 `public_repo` 权限。

**优先级**：shell 环境变量 > `.env` 文件（shell 里已有的不会被 `.env` 覆盖）

### 运行 / Run

从 skill 目录运行：

```bash
python3 search_agents.py
```

预计耗时：  
Expected runtime:
- 有 token：2-5 分钟
- 无 token：30-60 分钟（会触发限流频繁休眠）

### 常用选项 / Common Options

```bash
# 只取主榜前 30
python3 search_agents.py --top 30

# 跳过 README 读取（更快，但类型标签质量降）
python3 search_agents.py --skip-readme

# 自定义输出目录
python3 search_agents.py --output-dir /tmp/agent-report

# 选择输出语言：zh / en / bilingual
python3 search_agents.py --lang en

# 减少日志
python3 search_agents.py --quiet
```

### 输出文件 / Outputs

跑完后在 `output/` 目录（或 `--output-dir` 指定的目录）会得到：  
Output files are written to `output/` by default, or to `--output-dir` if provided.

| 文件                             | 用途                       |
| -------------------------------- | -------------------------- |
| `ranking-active-top50.md`        | 主榜 · 人类可读 Markdown   |
| `ranking-legacy-top20.md`        | 历史榜 · 补充生态演进脉络  |
| `ranking-all.csv`                | Excel 分析用，含 `desc_cn` / `desc_en` |
| `snapshot.json`                  | 完整结构化数据             |
| `metadata.json`                  | 本次跑批参数与输出文件索引 |
| `review-queue.md`                | 需要人工判定的边界 case    |

Markdown 榜单会在表格后附上每个项目的 `CN / EN` 一句话描述，方便直接复用到中文或英文材料里。  
Markdown files include `CN / EN` one-line summaries for each ranked project.

默认输出语言是 `bilingual`，也可以通过 `--lang zh|en|bilingual` 控制人类可读输出的文案语言。

## 方案概述 / Overview

完整方案见 [`DESIGN.md`](./DESIGN.md)。核心思路：

### 三路发现，兜住覆盖率
1. **关键词搜索**：15 个关键词并行搜（agent、coding-agent、copilot、agentic ...）
2. **Seed list 强制收录**：预置 60+ 已知 Agent 仓库（Cursor、Cline、Aider 等）
3. **Awesome 列表抽取**：从 6 个知名 Awesome 列表的 README 里正则抽取仓库链接

三路合并去重后进入候选池，预计覆盖率 80-85%。

### 两份榜单，不打架
- 活跃榜按 star 排；历史榜宽松纳入 Auto-GPT 等
- 每份榜单排序标准清晰、数据源明确

### 自动打标 + 人审兜底
- 15 个关键词打形态标签（CLI、IDE-plugin、AI-IDE、Web-Builder 等）
- 为上榜项目生成中英双语一句话描述
- 打不上标签的进入 `review-queue.md` 等人工判定

## 配置文件（可以编辑） / Editable Config Files

所有配置在 `data/` 目录，都是 JSON，可直接编辑：

- **`keywords.json`** — 搜索关键词。新出流行词就加进来
- **`seed-repos.json`** — Seed list。发现新 Agent 就加进 `product_agents` / `frameworks` / `middleware_and_tools` 三个数组之一
- **`awesome-lists.json`** — Awesome 列表源。发现新的高质量列表就加
- **`exclude-patterns.json`** — 排除规则。发现误抓就加

### 维护建议 / Maintenance Tips

- **每季度**过一遍 seed-repos.json，添加新出现的 Agent
- **每次跑之后**打开 `review-queue.md`，判断边界 case 是否要收录

## 已知局限 / Limitations

**产品设计时明确不解决的问题**：

- ❌ 只覆盖 GitHub（不含 GitLab / Gitee / 私有仓库）
- ❌ 覆盖率约 80-85%（长尾必然会漏）
- ❌ 快照式数据（不呈现 star 增长速度）
- ❌ 自动分类基于关键词，可能误判

**在公开文档或分享材料里应主动声明这些局限**，避免过度自信。

## 开源仓库使用建议 / Open Source Usage Notes

- GitHub 首页说明请优先看 [`README.md`](./README.md)
- 方法设计与边界说明见 [`DESIGN.md`](./DESIGN.md)
- 如果你要长期维护榜单，建议把 `data/` 下的 JSON 配置和 `output/` 下的生成结果分开管理

## 常见问题 / FAQ

**Q: 为什么某某项目没在榜上？**

依次检查：
1. star 数是否 ≥ 100（默认下限）
2. 是否有近 6 个月的 push（否则进历史榜）
3. 是否被排除规则误伤 → 查 `snapshot.json` 的 `excluded` 部分

**Q: 为什么榜上有一些我不认为是 Agent 的项目？**

- 打开 `review-queue.md` 检查 `type=?` 或 `form=?` 的项目
- 修改 `exclude-patterns.json` 增加规则
- 重跑

**Q: 能不能加代理 / 换镜像 / 走内网？**

- 已内置对 `http_proxy` / `https_proxy` 环境变量的支持
- 如需自定义 GitHub API 域名，可自行调整 `API_BASE` 常量

## 版本

v1.0（2026-07-14）· 首个可运行版本
