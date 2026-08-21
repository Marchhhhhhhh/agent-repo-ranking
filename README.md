<div align="center">

# agent-repo-ranking

[中文](./README.zh.md) | **English**

</div>

Generate ranked snapshots of the GitHub agent ecosystem with reproducible filters, structured outputs, and review queues.

It produces two lists:

- Active list: GitHub agent repositories with commits in the last 180 days
- Legacy list: historically important but recently inactive GitHub agent repositories

## What It Does

- Uses keyword search, seed repos, and Awesome lists to discover candidates
- Applies basic filters, tagging rules, and manual review queues
- Generates one-line descriptions in both Chinese and English
- Exports Markdown, CSV, JSON, and review artifacts

## Quick Start

### Requirements

- Python 3.9+ (standard library only, no third-party dependencies)

### 1. Prepare a GitHub Token

A GitHub Personal Access Token is strongly recommended, otherwise rate limits will be severe.

```bash
cp .env.example .env
```

Replace the placeholder in `.env` with your token:

```bash
GITHUB_TOKEN=ghp_your_token_here
```

You can also use an environment variable:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

### 2. Run the Script

```bash
python3 search_agents.py
```

Common options:

```bash
# Generate top 30 active repos
python3 search_agents.py --top 30

# Generate top 15 legacy repos
python3 search_agents.py --legacy 15

# Skip web-search classification (faster, lower label quality)
python3 search_agents.py --skip-search

# Set a custom output directory
python3 search_agents.py --output-dir /tmp/agent-report

# Choose output language: zh / en / bilingual
python3 search_agents.py --lang en

# Reduce logs
python3 search_agents.py --quiet
```

## Outputs

By default, files are written to `output/`.

- `ranking-active-top<N>-YYYY-MM-DD.md`
- `ranking-legacy-top<N>-YYYY-MM-DD.md`
- `ranking-all-YYYY-MM-DD.csv`
- `snapshot-YYYY-MM-DD.json`
- `metadata-YYYY-MM-DD.json`
- `review-queue-YYYY-MM-DD.md`

Notes:

- The default output language is `bilingual`
- Markdown files include one-line summaries in both `CN` and `EN`
- CSV includes both `desc_cn` and `desc_en`

## Configuration

All configs live under `data/`.

- `keywords.json`: search keywords
- `seed-repos.json`: forced-include repo seeds
- `awesome-lists.json`: Awesome lists used for discovery
- `exclude-patterns.json`: exclusion rules

## Good For

- Quickly mapping the agent ecosystem
- Generating reproducible snapshots for talks, reports, or research
- Maintaining an ecosystem watchlist with manual review hooks

## Method

See [DESIGN.md](./DESIGN.md) for the full methodology.

This project is designed as a practical engineering workflow, not a perfect research database.

## Limitations

- Only covers public GitHub repositories
- Automatic classification is keyword and README based, so false positives are possible
- Results are snapshots, not long-term trend analysis

## Proxy Support

The script automatically reads `http_proxy` and `https_proxy`.

## Security

- Never commit real tokens
- Confirm `.env` is not tracked before publishing
- If a real token ever appeared in examples, rotate it immediately

## License

[MIT](./LICENSE)
