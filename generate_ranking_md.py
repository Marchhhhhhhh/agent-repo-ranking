#!/usr/bin/env python3
"""Regenerate ranking-en.md / ranking-zh.md from the latest bilingual output files.

Zero-dependency (stdlib only), same as search_agents.py.

What it does:
1. Finds the two most recent dated snapshots (output/ranking-all-YYYY-MM-DD.csv).
2. Parses the latest output/ranking-active-top50-*.md and ranking-products-top50-*.md.
3. Writes ranking-en.md and ranking-zh.md at the repo root, including a
   "Recently Surging / 近期飙升" section (star gains vs the previous snapshot)
   and a "Newly Added / 新收录" subsection (repos entering the dataset for the
   first time).

Usage: python3 generate_ranking_md.py
"""
import csv
import glob
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "output")

SURGE_MIN_GAIN = 1000      # min star gain to be listed as surging
SURGE_TOP_N = 10           # max rows in the surging table
NEW_MIN_STARS = 5000       # min stars for newly added repos to be listed
NEW_TOP_N = 10             # max rows in the newly-added table
DESC_MAX_LEN = 140         # truncate descriptions in surging/new tables


def strip_emoji(s):
    out = []
    for ch in s:
        cp = ord(ch)
        if (0x1F000 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) \
                or cp in (0xFE0F, 0x200D) or (0x1F1E6 <= cp <= 0x1F1FF):
            continue
        out.append(ch)
    s = "".join(out)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def truncate(s, limit=DESC_MAX_LEN):
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def norm_cell(s):
    s = s.strip()
    if s in ("-", "?", ""):
        return "—"
    if s == "Apache-2.0":
        return "Apache 2.0"
    return s


def parse_ranking_md(path):
    """Parse a bilingual output ranking md -> (snapshot_str, table_rows, descs)."""
    text = open(path, encoding="utf-8").read()
    snap = re.search(r"Data Snapshot / 数据快照\*\*: (.+)", text).group(1).strip()
    rows, descs, cur = [], {}, None
    for line in text.splitlines():
        if line.startswith("| ") and "](" in line:
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        m = re.match(r"^\d+\. \*\*(.+?)\*\*$", line)
        if m:
            cur = m.group(1)
            descs[cur] = {}
        elif cur and line.strip().startswith("- CN:"):
            descs[cur]["CN"] = strip_emoji(line.split("- CN:", 1)[1].strip())
        elif cur and line.strip().startswith("- EN:"):
            descs[cur]["EN"] = strip_emoji(line.split("- EN:", 1)[1].strip())
    return snap, rows, descs


def repo_of(link_cell):
    return re.search(r"\[(.+?)\]\(", link_cell).group(1)


def load_csv(path):
    """full_name -> dict(rank, stars, desc_cn, desc_en)."""
    data = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            data[r["full_name"]] = {
                "rank": int(r["rank"]),
                "stars": int(r["stars"]),
                "desc_cn": strip_emoji(r.get("desc_cn", "") or r.get("description", "")),
                "desc_en": strip_emoji(r.get("desc_en", "") or r.get("description", "")),
            }
    return data


def find_snapshot_dates():
    dates = sorted(
        re.search(r"ranking-all-(\d{4}-\d{2}-\d{2})\.csv$", p).group(1)
        for p in glob.glob(os.path.join(OUT, "ranking-all-*.csv"))
    )
    if len(dates) < 2:
        raise SystemExit("Need at least two dated ranking-all-*.csv snapshots")
    return dates[-2], dates[-1]


def md_link(name):
    return f"[{name}](https://github.com/{name})"


def compute_surging(prev, cur):
    """Repos in both snapshots, sorted by star gain."""
    surging = []
    for name, c in cur.items():
        p = prev.get(name)
        if not p:
            continue
        gain = c["stars"] - p["stars"]
        if gain >= SURGE_MIN_GAIN:
            surging.append((gain, name, p, c))
    surging.sort(key=lambda x: -x[0])
    return surging[:SURGE_TOP_N]


def compute_newly_added(prev, cur):
    """Repos appearing for the first time in the dataset."""
    new = [(c["stars"], name, c) for name, c in cur.items()
           if name not in prev and c["stars"] >= NEW_MIN_STARS]
    new.sort(key=lambda x: -x[0])
    return new[:NEW_TOP_N]


def surging_section_en(surging, newly, prev_date, cur_date):
    lines = [
        "## Recently Surging",
        "",
        f"> Biggest star gains between the {prev_date} and {cur_date} snapshots "
        "(rank refers to the full all-repo list)",
        "",
        "| Project | +Stars | Stars | Rank | Description |",
        "|---------|--------|-------|------|-------------|",
    ]
    for gain, name, p, c in surging:
        lines.append(
            f"| {md_link(name)} | +{gain:,} | {c['stars']:,} | "
            f"{p['rank']} → {c['rank']} | {truncate(c['desc_en'])} |"
        )
    lines += [
        "",
        "### Newly Added to the Dataset",
        "",
        "> Notable repos included for the first time in this snapshot",
        "",
        "| Project | Stars | Rank | Description |",
        "|---------|-------|------|-------------|",
    ]
    for stars, name, c in newly:
        lines.append(
            f"| {md_link(name)} | {stars:,} | {c['rank']} | {truncate(c['desc_en'])} |"
        )
    return "\n".join(lines)


def surging_section_zh(surging, newly, prev_date, cur_date):
    lines = [
        "## 近期飙升",
        "",
        f"> 对比 {prev_date} 与 {cur_date} 两期快照，星数增长最多的仓库"
        "（排名为全量总榜名次）",
        "",
        "| 项目 | 星数增量 | 当前星数 | 总榜排名 | 简介 |",
        "|------|---------|---------|---------|------|",
    ]
    for gain, name, p, c in surging:
        lines.append(
            f"| {md_link(name)} | +{gain:,} | {c['stars']:,} | "
            f"{p['rank']} → {c['rank']} | {truncate(c['desc_cn'])} |"
        )
    lines += [
        "",
        "### 新收录仓库",
        "",
        "> 本期新进入数据集的高星仓库",
        "",
        "| 项目 | 星数 | 总榜排名 | 简介 |",
        "|------|------|---------|------|",
    ]
    for stars, name, c in newly:
        lines.append(
            f"| {md_link(name)} | {stars:,} | {c['rank']} | {truncate(c['desc_cn'])} |"
        )
    return "\n".join(lines)


def main():
    prev_date, cur_date = find_snapshot_dates()

    snap_a, rows_a, descs_a = parse_ranking_md(
        os.path.join(OUT, f"ranking-active-top50-{cur_date}.md"))
    snap_p, rows_p, _ = parse_ranking_md(
        os.path.join(OUT, f"ranking-products-top50-{cur_date}.md"))
    assert snap_a == snap_p, (snap_a, snap_p)
    assert len(rows_a) == 50 and len(rows_p) == 50, (len(rows_a), len(rows_p))

    prev, cur = load_csv(os.path.join(OUT, f"ranking-all-{prev_date}.csv")), \
        load_csv(os.path.join(OUT, f"ranking-all-{cur_date}.csv"))
    surging, newly = compute_surging(prev, cur), compute_newly_added(prev, cur)

    def active_row(c):
        return (f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} | {norm_cell(c[4])} | "
                f"{norm_cell(c[5])} | {norm_cell(c[6])} | {c[7]} |")

    def product_row(c):
        return (f"| {c[0]} | {c[1]} | {c[2]} | {c[3]} | {norm_cell(c[4])} | "
                f"{norm_cell(c[5])} | {c[6]} |")

    active_table = "\n".join(active_row(c) for c in rows_a)
    product_table = "\n".join(product_row(c) for c in rows_p)

    order = [repo_of(c[1]) for c in rows_a]
    missing = [r for r in order
               if r not in descs_a or "EN" not in descs_a[r] or "CN" not in descs_a[r]]
    assert not missing, missing
    desc_en = "\n".join(
        f"{i}. **{r}** - {descs_a[r]['EN']}" for i, r in enumerate(order, 1))
    desc_zh = "\n".join(
        f"{i}. **{r}** - {descs_a[r]['CN']}" for i, r in enumerate(order, 1))

    surge_en = surging_section_en(surging, newly, prev_date, cur_date)
    surge_zh = surging_section_zh(surging, newly, prev_date, cur_date)

    en = f"""# GitHub Agent Ecosystem Rankings

> **Data Snapshot**: {snap_a}
> **Notes**: Active Top 50 and Products Top 50, covering coding agents, autonomous agents, multi-agent frameworks, MCP tooling and more.

---

## Main · Active Ecosystem Top 50

> Repos pushed within the last 180 days, ranked by stars

| # | Project | Stars | Forks | Type | Language | License | Last Push |
|---|---------|-------|-------|------|----------|---------|-----------|
{active_table}

---

## Products Top 50

> End-user agent products only (no frameworks / middleware), ranked by stars

| # | Project | Stars | Forks | Language | License | Last Push |
|---|---------|-------|-------|----------|---------|-----------|
{product_table}

---

{surge_en}

---

### One-line Descriptions

{desc_en}

---

*Generated by [agent-repo-ranking](https://github.com/Marchhhhhhhh/agent-repo-ranking)*
"""

    zh = f"""# GitHub Agent 生态排名

> **数据快照**: {snap_a}
> **说明**: 活跃生态 Top 50 与纯产品 Top 50，涵盖编码助手、自主 Agent、多智能体框架、MCP 工具链等。

---

## 主榜 · 活跃生态 Top 50

> 仅收录最近 180 天内有 push 的仓库，按 star 排序

| # | 项目 | Stars | Forks | 类型 | 语言 | 许可证 | 最近提交 |
|---|------|-------|-------|------|------|--------|----------|
{active_table}

---

## 副榜 · 纯产品 Top 50

> 仅收录终端可用的 Agent 产品（不含框架 / 中间件），按 star 排序

| # | 项目 | Stars | Forks | 语言 | 许可证 | 最近提交 |
|---|------|-------|-------|------|--------|----------|
{product_table}

---

{surge_zh}

---

### 一句话描述

{desc_zh}

---

*由 [agent-repo-ranking](https://github.com/Marchhhhhhhh/agent-repo-ranking) 生成*
"""

    with open(os.path.join(ROOT, "ranking-en.md"), "w", encoding="utf-8") as f:
        f.write(en)
    with open(os.path.join(ROOT, "ranking-zh.md"), "w", encoding="utf-8") as f:
        f.write(zh)
    print(f"OK {snap_a} (surging vs {prev_date}: {len(surging)} rows, "
          f"newly added: {len(newly)} rows)")


if __name__ == "__main__":
    main()
