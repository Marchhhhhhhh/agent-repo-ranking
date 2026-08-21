<div align="center">

# agent-repo-ranking

**中文** | [English](./README.md)

</div>

一个用于生成 GitHub Agent 生态排名快照的轻量工具，强调可复跑、可解释、可维护。

它会输出两份榜单：

- 活跃榜：近 180 天有提交的 GitHub Agent 仓库
- 历史榜：曾有影响力但近期不活跃的 GitHub Agent 仓库

## 这个项目做什么

- 通过关键词搜索、seed list、Awesome 列表三路发现候选仓库
- 自动做基础过滤、分类打标和人工复核队列
- 为项目生成中英双语一句话描述
- 输出 Markdown、CSV、JSON 和 review 队列

## 快速开始

### 环境要求

- Python 3.9+（只用标准库，零第三方依赖）

### 1. 准备 GitHub Token

强烈建议配置 GitHub Personal Access Token，否则限流会比较严重。

```bash
cp .env.example .env
```

把 `.env` 里的占位符替换成你自己的 token：

```bash
GITHUB_TOKEN=ghp_your_token_here
```

也可以直接使用环境变量：

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

### 2. 运行脚本

```bash
python3 search_agents.py
```

常用参数：

```bash
# 只生成前 30 的活跃榜
python3 search_agents.py --top 30

# 历史榜取前 15
python3 search_agents.py --legacy 15

# 指定输出目录
python3 search_agents.py --output-dir /tmp/agent-report

# 选择输出语言：zh / en / bilingual
python3 search_agents.py --lang zh

# 减少日志
python3 search_agents.py --quiet
```

## 输出文件

默认输出到 `output/` 目录。

- `ranking-active-top<N>-YYYY-MM-DD.md`
- `ranking-legacy-top<N>-YYYY-MM-DD.md`
- `ranking-all-YYYY-MM-DD.csv`
- `snapshot-YYYY-MM-DD.json`
- `metadata-YYYY-MM-DD.json`
- `review-queue-YYYY-MM-DD.md`

补充说明：

- 默认输出语言是 `bilingual`
- Markdown 榜单会附带 `CN / EN` 两份一句话描述
- CSV 会额外包含 `desc_cn` 和 `desc_en` 两列

## 配置说明

所有配置文件都在 `data/` 目录下。

- `keywords.json`：搜索关键词
- `seed-repos.json`：强制收录的已知仓库
- `awesome-lists.json`：用于发现候选仓库的 Awesome 列表
- `exclude-patterns.json`：排除规则
- `type-overrides.json`：AI/人工确认的类型映射（分类最高优先级）

## 适用场景

- 快速盘点 Agent 生态
- 为分享、文章、研究或内部汇报生成可复跑的数据快照
- 维护一个带人工复核入口的 Agent 仓库观察清单

## 方法说明

完整方法设计见 [DESIGN.md](./DESIGN.md)。

这个项目更偏向可复跑、可解释、可维护的工程方案，而不是追求绝对完整的研究数据库。

## 已知局限

- 只覆盖 GitHub 公开仓库
- 自动分类基于关键词和 README，存在误判可能
- 数据是快照，不代表长期趋势

## 代理支持

脚本会自动读取 `http_proxy` / `https_proxy` 环境变量。

## 安全建议

- 不要把真实 token 写入仓库
- 首次公开前，确认 `.env` 没有被提交
- 如果曾把真实 token 放进示例文件，请先去 GitHub 立即轮换

## License

[MIT](./LICENSE)
