#!/usr/bin/env python3
"""
GitHub Agent repository ranking generator.

Collects Agent-related repositories from GitHub and generates two ranked outputs:
  · Active Top N
  · Legacy Top N

依据 DESIGN.md 中的方法，从 GitHub 收集 Agent 相关仓库并生成两份榜单：
  · 主榜 · 活跃 Top N
  · 副榜 · 历史 Top N（含已停维护）

Usage / 用法:
  python3 search_agents.py                          # Run with defaults / 用默认配置跑
  python3 search_agents.py --top 30 --legacy 15     # Customize ranking sizes / 自定义榜长
  python3 search_agents.py --output-dir out/        # Set output directory / 自定义输出目录
  python3 search_agents.py --lang en                # Output in English / 输出英文文案

Environment / 环境变量:
  GITHUB_TOKEN     GitHub Personal Access Token（strongly recommended / 强烈建议）
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib import request, error, parse

# ---------------------------------------------------------------------------
# 配置与常量
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"


def load_dotenv(path: Path) -> None:
    """极简 .env 加载器，只处理 KEY=VALUE 格式，不依赖第三方库"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 不覆盖已存在的环境变量（例如通过 shell export 手动设置的优先）
        if key and key not in os.environ:
            os.environ[key] = value


# 启动时自动加载 skill 目录下的 .env
load_dotenv(SCRIPT_DIR / ".env")


API_BASE = "https://api.github.com"
USER_AGENT = "agent-repo-ranking/1.1"


def build_url_opener() -> request.OpenerDirector:
    """构建 urllib opener，自动识别环境变量里的 http/https 代理。

    支持 http_proxy / https_proxy（大小写均可）。all_proxy 若是 socks5，
    urllib 原生不支持，此处忽略并回退到 http(s) 代理（GitHub API 走 https 即可）。
    """
    env = os.environ
    http_proxy = env.get("http_proxy") or env.get("HTTP_PROXY")
    https_proxy = env.get("https_proxy") or env.get("HTTPS_PROXY")

    proxies: dict[str, str] = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy

    if proxies:
        handler = request.ProxyHandler(proxies)
        opener = request.build_opener(handler)
        print(
            f"🌐 已启用代理：{proxies}",
            file=sys.stderr,
        )
    else:
        opener = request.build_opener()
    return opener


def active_ranking_filename(top_n: int, date_str: str) -> str:
    return f"ranking-active-top{top_n}-{date_str}.md"


def legacy_ranking_filename(top_n: int, date_str: str) -> str:
    return f"ranking-legacy-top{top_n}-{date_str}.md"


def product_ranking_filename(top_n: int, date_str: str) -> str:
    return f"ranking-products-top{top_n}-{date_str}.md"


# 全局 opener（在 main 中根据环境变量初始化）
_OPENER: request.OpenerDirector = request.build_opener()

# 主榜 / 历史榜的活跃度判定阈值：pushed_at 在多少天内算"活跃"
ACTIVE_DAYS_THRESHOLD = 180  # 6 个月

# 每个关键词最多抓多少条（分页上限）
MAX_PER_KEYWORD = 300  # GitHub search 单次查询最多 1000 条，取前 300 已够


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Repo:
    """一个 GitHub 仓库的完整记录"""

    full_name: str  # owner/name
    owner: str
    name: str
    url: str
    description: str
    stars: int
    forks: int
    language: str | None
    license: str | None
    is_archived: bool
    is_fork: bool
    created_at: str
    pushed_at: str
    # 计算字段
    is_active: bool = False
    days_since_push: int = 0
    # 分类
    type: str = "?"  # Product / Framework / Middleware / ?
    form: str | None = None  # CLI / IDE-plugin / AI-IDE / ...
    auto_tags: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    desc_cn: str = ""  # 一句话中文描述
    desc_en: str = ""  # One-line English description
    # 元数据
    discovery_source: list[str] = field(default_factory=list)
    review_status: str = "auto"  # auto / needs_review / manual
    readme_excerpt: str = ""

    @property
    def key(self) -> str:
        return self.full_name.lower()


# ---------------------------------------------------------------------------
# GitHub API 客户端
# ---------------------------------------------------------------------------


class GitHubClient:
    """一个极简的 GitHub REST API 封装，只依赖标准库

    注意：GitHub 的 /search/* 和 /repos/* 使用两套独立限流：
      · /search/* : 30 请求/分钟（登录）/ 10 请求/分钟（未登录）
      · /repos/*  : 5000/小时（登录）/ 60/小时（未登录）
    因此这里对两种端点分别跟踪 remaining。
    """

    def __init__(self, token: str | None = None, verbose: bool = True):
        self.token = token
        self.verbose = verbose
        # 两个独立的 rate limit 桶
        self._search_remaining = 30 if token else 10
        self._search_reset = 0
        self._core_remaining = 5000 if token else 60
        self._core_reset = 0

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _is_search_endpoint(self, path: str) -> bool:
        return path.startswith("/search/")

    def _wait_for_rate_limit(self, path: str) -> None:
        """按端点类型决定该看哪个限流桶"""
        if self._is_search_endpoint(path):
            remaining = self._search_remaining
            reset = self._search_reset
            label = "search"
        else:
            remaining = self._core_remaining
            reset = self._core_reset
            label = "core"

        if remaining <= 1 and reset:
            wait = max(0, reset - int(time.time())) + 2
            # 上限：单次等待最多 90 秒，超过就分批睡（避免锁死 1 小时）
            if wait > 90:
                self._log(
                    f"  ⚠️  {label} rate limit requires {wait}s (~{wait//60} min) / 需等 {wait}s（近 {wait//60} 分钟）。"
                    f"This usually means GITHUB_TOKEN is missing or exhausted. / 这通常意味着 token 未配置或已耗尽。请检查 GITHUB_TOKEN。"
                )
                self._log("  ⏳ Sleeping in chunks of 60s, Ctrl+C to abort... / 分批睡眠中，每次 60s，可 Ctrl+C 中止...")
                slept = 0
                while slept < wait:
                    time.sleep(min(60, wait - slept))
                    slept += 60
                    self._log(f"     ...waited {slept}s / {wait}s / 已等 {slept}s / {wait}s")
            elif wait > 0:
                self._log(f"  ⏳ {label} rate limit hit, sleeping {wait}s ...")
                time.sleep(wait)

    def get(self, path: str, params: dict[str, Any] | None = None, _retries: int = 0) -> dict | list:
        """通用 GET，自动处理速率限制"""
        self._wait_for_rate_limit(path)

        url = f"{API_BASE}{path}"
        if params:
            url = f"{url}?{parse.urlencode(params)}"

        req = request.Request(url, headers=self._headers())
        try:
            with _OPENER.open(req, timeout=30) as resp:
                # 按端点类型更新对应的 rate limit
                try:
                    remaining = int(resp.headers.get("X-RateLimit-Remaining", -1))
                    reset = int(resp.headers.get("X-RateLimit-Reset", 0))
                    if remaining >= 0:
                        if self._is_search_endpoint(path):
                            self._search_remaining = remaining
                            self._search_reset = reset
                        else:
                            self._core_remaining = remaining
                            self._core_reset = reset
                except (TypeError, ValueError):
                    pass
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except error.HTTPError as e:
            if e.code == 403 and "rate limit" in e.read().decode("utf-8", errors="ignore").lower():
                self._log("  ⚠️  hit hard rate limit, sleeping 60s / 触发硬限流，睡眠 60s")
                time.sleep(60)
                return self.get(path, params, _retries)
            if e.code == 404:
                self._log(f"  ⚠️  404: {path}")
                return {}
            raise
        except (error.URLError, TimeoutError, OSError, IncompleteRead) as e:
            if _retries >= 3:
                self._log(f"  ⚠️  network error on {path}: {e}. Retried {_retries} times, giving up / 已重试 {_retries} 次，放弃")
                return {}
            self._log(f"  ⚠️  network error on {path}: {e}. retry in 5s ({_retries + 1}/3)")
            time.sleep(5)
            return self.get(path, params, _retries + 1)

    def search_repos(self, query: str, per_page: int = 100, max_results: int = 300) -> list[dict]:
        """搜索仓库，自动翻页"""
        results = []
        pages = (max_results + per_page - 1) // per_page
        for page in range(1, pages + 1):
            data = self.get(
                "/search/repositories",
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                break
            results.extend(items)
            if len(items) < per_page:
                break
        return results[:max_results]

    def get_repo(self, full_name: str) -> dict | None:
        data = self.get(f"/repos/{full_name}")
        return data if isinstance(data, dict) and data.get("id") else None

    def get_readme(self, full_name: str) -> str:
        data = self.get(f"/repos/{full_name}/readme")
        if not isinstance(data, dict) or "content" not in data:
            return ""
        try:
            raw = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
            return raw
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# 数据文件加载
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found / 配置文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(data_dir: Path) -> dict:
    overrides_raw = load_json(data_dir / "type-overrides.json")
    return {
        "keywords": load_json(data_dir / "keywords.json"),
        "seed": load_json(data_dir / "seed-repos.json"),
        "awesome": load_json(data_dir / "awesome-lists.json"),
        "exclude": load_json(data_dir / "exclude-patterns.json"),
        # AI/人工确认的类型映射，key 统一转小写便于匹配
        "type_overrides": {k.lower(): v for k, v in overrides_raw.get("overrides", {}).items()},
    }


# ---------------------------------------------------------------------------
# 发现阶段：三条路径并行收集候选仓库
# ---------------------------------------------------------------------------


def discover_via_keywords(gh: GitHubClient, keywords: list[str]) -> dict[str, dict]:
    """路径 A：关键词搜索"""
    pool: dict[str, dict] = {}
    for kw in keywords:
        gh._log(f"  🔍 Searching keyword / 搜索关键词：'{kw}'")
        items = gh.search_repos(kw, max_results=MAX_PER_KEYWORD)
        for it in items:
            key = it["full_name"].lower()
            if key in pool:
                # 记录多个发现来源
                pool[key].setdefault("_sources", []).append(f"keyword:{kw}")
            else:
                it["_sources"] = [f"keyword:{kw}"]
                pool[key] = it
        gh._log(f"    → Accumulated {len(pool)} unique repos / 累计 {len(pool)} 个不重复仓库")
    return pool


def discover_via_seed(gh: GitHubClient, seed_config: dict) -> dict[str, dict]:
    """路径 B：seed list 强制收录"""
    pool: dict[str, dict] = {}
    all_seeds = (
        seed_config.get("product_agents", [])
        + seed_config.get("frameworks", [])
        + seed_config.get("middleware_and_tools", [])
    )
    gh._log(f"  📋 Seed list has {len(all_seeds)} repos / seed list 共 {len(all_seeds)} 个仓库")
    for full_name in all_seeds:
        data = gh.get_repo(full_name)
        if data:
            key = data["full_name"].lower()
            data["_sources"] = ["seed-list"]
            pool[key] = data
    gh._log(f"    → Got metadata for {len(pool)} seed repos / 拿到 {len(pool)} 个 seed 仓库元数据")
    return pool


REPO_URL_PATTERN = re.compile(
    r"github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:[/#)\s.]|\.git|$)"
)


def discover_via_awesome(gh: GitHubClient, awesome_config: dict) -> set[str]:
    """路径 C：Awesome 列表抽取仓库 URL"""
    found: set[str] = set()
    for entry in awesome_config.get("lists", []):
        repo = entry["repo"]
        gh._log(f"  📚 Extracting Awesome list / 抽取 Awesome 列表：{repo}")
        readme = gh.get_readme(repo)
        if not readme:
            continue
        for m in REPO_URL_PATTERN.finditer(readme):
            owner, name = m.group(1), m.group(2).rstrip(".")
            # 过滤明显不是仓库的路径（如 github.com/features/copilot）
            if owner.lower() in {"features", "sponsors", "orgs", "topics", "settings", "marketplace"}:
                continue
            if name.lower() in {"", "readme.md"}:
                continue
            found.add(f"{owner}/{name}".lower())
    gh._log(f"    → Extracted {len(found)} URLs from Awesome lists / 从 Awesome 列表抽出 {len(found)} 个 URL")
    return found


def enrich_awesome_urls(gh: GitHubClient, urls: set[str], existing: dict[str, dict]) -> None:
    """对 Awesome 列表发现的 URL，如果不在池中，则拉取元数据加入"""
    added = 0
    for full_name in urls:
        if full_name in existing:
            existing[full_name].setdefault("_sources", []).append("awesome-list")
            continue
        data = gh.get_repo(full_name)
        if data:
            key = data["full_name"].lower()
            data["_sources"] = ["awesome-list"]
            existing[key] = data
            added += 1
            if added % 20 == 0:
                gh._log(f"    → Enriched {added} awesome-discovered repos / 已补齐 {added} 个 awesome 发现的仓库元数据")
    gh._log(f"    → Added {added} new repos from Awesome lists / 从 awesome 列表新增 {added} 个仓库")


# ---------------------------------------------------------------------------
# 过滤阶段
# ---------------------------------------------------------------------------


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def should_exclude(repo: dict, exclude_config: dict, seed_keys: set[str]) -> tuple[bool, str]:
    """判断一个仓库是否应该被排除。返回 (是否排除, 原因)"""

    full_name = repo.get("full_name", "").lower()
    name = repo.get("name", "").lower()
    desc = (repo.get("description") or "").lower()
    topics = [t.lower() for t in (repo.get("topics") or [])]

    # seed list 强制收录
    if full_name in seed_keys:
        return False, ""

    # hard exclude by full_name
    if full_name in {s.lower() for s in exclude_config.get("hard_exclude_full_names", [])}:
        return True, "hard_exclude:full_name"

    # hard exclude by name pattern
    for pat in compile_patterns(exclude_config.get("hard_exclude_name_patterns", [])):
        if pat.search(name):
            return True, f"hard_exclude:name~{pat.pattern}"

    # hard exclude by description
    for kw in exclude_config.get("hard_exclude_description_keywords", []):
        if kw.lower() in desc:
            return True, f"hard_exclude:desc~{kw}"

    # min stars
    if repo.get("stargazers_count", 0) < exclude_config.get("min_stars", 100):
        return True, "min_stars"

    # archived
    if exclude_config.get("exclude_archived", True) and repo.get("archived"):
        return True, "archived"

    # fork（除非明显比 upstream 活跃，暂时统一排）
    if exclude_config.get("exclude_forks", True) and repo.get("fork"):
        return True, "fork"

    # soft exclude：描述里包含不相关关键词
    for kw in exclude_config.get("soft_exclude_description_keywords", []):
        if kw.lower() in desc:
            return True, f"soft_exclude:desc~{kw}"

    # 正向 Agent 信号要求：topics / 名称 / 描述里必须有 agent 相关信号，
    # 否则判为"疑似非 Agent"，剔除（README 阶段无法回补，因为过滤在读 README 前）。
    if exclude_config.get("require_agent_signal", False):
        if not has_agent_signal(name, desc, topics, exclude_config):
            return True, "no_agent_signal"

    return False, ""


def has_agent_signal(name: str, desc: str, topics: list[str], exclude_config: dict) -> bool:
    """判断一个仓库是否带有 Agent 相关的正向信号（topics / 名称 / 描述）"""
    signal_topics = {t.lower() for t in exclude_config.get("agent_signal_topics", [])}
    if signal_topics & set(topics):
        return True

    signal_text = [t.lower() for t in exclude_config.get("agent_signal_text", [])]
    haystack = f"{name} {desc}"
    if any(sig in haystack for sig in signal_text):
        return True

    return False


# ---------------------------------------------------------------------------
# 归一化 + 打标
# ---------------------------------------------------------------------------


def parse_iso_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize(raw: dict) -> Repo:
    """从 GitHub API 的 raw 数据转成 Repo 对象"""
    pushed = raw.get("pushed_at", "")
    pushed_dt = parse_iso_datetime(pushed)
    now = datetime.now(timezone.utc)
    days_since = (now - pushed_dt).days if pushed_dt else 999
    is_active = days_since <= ACTIVE_DAYS_THRESHOLD

    license_info = raw.get("license") or {}
    license_name = license_info.get("spdx_id") if isinstance(license_info, dict) else None

    return Repo(
        full_name=raw["full_name"],
        owner=raw["owner"]["login"] if isinstance(raw.get("owner"), dict) else raw.get("full_name", "").split("/")[0],
        name=raw["name"],
        url=raw.get("html_url", f"https://github.com/{raw['full_name']}"),
        description=raw.get("description") or "",
        stars=raw.get("stargazers_count", 0),
        forks=raw.get("forks_count", 0),
        language=raw.get("language"),
        license=license_name,
        is_archived=bool(raw.get("archived")),
        is_fork=bool(raw.get("fork")),
        created_at=raw.get("created_at", ""),
        pushed_at=pushed,
        is_active=is_active,
        days_since_push=days_since,
        topics=raw.get("topics") or [],
        discovery_source=raw.get("_sources", []),
    )


# 类型 / 形态自动打标规则
TYPE_RULES = {
    "Framework": [
        "framework for",
        "framework to",
        "framework that",
        "agentic framework",
        "agent framework",
        "build your own",
        "build agents",
        "building agents",
        "library for",
        "sdk for",
        "python package",
        "toolkit for",
        "toolkit:",
        "orchestration framework",
        "multi-agent framework",
        "harness",
        "build llm",
        "building llm",
    ],
    "Middleware": [
        "mcp server",
        "evaluation",
        "observability",
        "guardrail",
        "safety scanner",
        "memory layer",
        "long-term memory",
        "persistent context",
        "tracing",
        "monitoring for",
        "sandbox for",
        "infrastructure for",
        "gateway",
        "proxy for",
    ],
    "Product": [
        "install",
        "extension for vscode",
        "download the app",
        "cli tool",
        "coding agent that",
        "ai assistant",
        "ai coding assistant",
        "coding assistant",
        "pair programmer",
        "autonomous software engineer",
        "software engineer",
        "desktop app",
        "personal assistant",
        "operates your browser",
        "operate the browser",
        "control your browser",
        "自主软件工程",
        "编码助手",
    ],
}

FORM_RULES = {
    "IDE-plugin": ["vscode extension", "extension for vscode", "jetbrains plugin", "ide extension", "vs code extension"],
    "AI-IDE": ["fork of vscode", "code editor", "ai-powered editor", "ide fork", "ai code editor", "ai-native editor"],
    "CLI": ["command line", "cli tool", "terminal-based", "run in terminal", "command-line", "terminal ui", "tui", "in your terminal", "命令行", "终端"],
    "Web-Builder": ["natural language to app", "app builder", "generate ui", "generate a web app", "text to app", "build web apps", "design engine"],
    "Cloud-General": ["autonomous cloud", "cloud runtime", "async task", "long-horizon", "runs in the cloud", "cloud agent"],
    "Multi-Agent-Framework": ["multi-agent", "multi agent", "multiple agents", "agent teams", "crew of agents", "multi-agents", "subagent", "sub-agent"],
    "Browser-Agent": ["browser automation", "operate the browser", "control the browser", "web automation", "browser-use", "operates websites", "browse the web"],
    "MCP-Ecosystem": ["mcp server", "model context protocol", "mcp servers"],
    "Self-Hosted": ["self-hosted", "self host", "run on your own", "self-hostable", "local-first", "self-hosting"],
}

# 从 topics 直接映射类型 / 形态
TOPIC_TYPE_MAP = {
    "agent-framework": "Framework",
    "llm-framework": "Framework",
    "agentic-framework": "Framework",
    "framework": "Framework",
    "sdk": "Framework",
    "mcp": "Middleware",
    "model-context-protocol": "Middleware",
    "observability": "Middleware",
    "llmops": "Middleware",
    "vscode-extension": "Product",
    "copilot": "Product",
    "coding-assistant": "Product",
}

TOPIC_FORM_MAP = {
    "vscode-extension": "IDE-plugin",
    "vscode": "IDE-plugin",
    "jetbrains-plugin": "IDE-plugin",
    "cli": "CLI",
    "command-line": "CLI",
    "terminal": "CLI",
    "tui": "CLI",
    "multi-agent": "Multi-Agent-Framework",
    "multi-agents": "Multi-Agent-Framework",
    "browser-use": "Browser-Agent",
    "browser-automation": "Browser-Agent",
    "mcp": "MCP-Ecosystem",
    "model-context-protocol": "MCP-Ecosystem",
    "self-hosted": "Self-Hosted",
}


def _match_type_form(text: str, topics: list[str]) -> tuple[str, str | None]:
    """在给定文本 + topics 上执行 TYPE_RULES / FORM_RULES 匹配"""
    matched_type = None
    for t, keywords in TYPE_RULES.items():
        if any(kw in text for kw in keywords):
            matched_type = t
            break
    if not matched_type:
        for tp in topics:
            if tp in TOPIC_TYPE_MAP:
                matched_type = TOPIC_TYPE_MAP[tp]
                break

    matched_form = None
    for f, keywords in FORM_RULES.items():
        if any(kw in text for kw in keywords):
            matched_form = f
            break
    if not matched_form:
        for tp in topics:
            if tp in TOPIC_FORM_MAP:
                matched_form = TOPIC_FORM_MAP[tp]
                break

    return matched_type or "?", matched_form


def classify(repo: Repo, overrides: dict[str, Any] | None = None) -> None:
    """打标签：data/type-overrides.json（AI/人工确认）最高优先级，description + topics 兜底。

    工作流：跑批产出 review-queue 后，由运行 skill 的 AI 用自带的联网搜索能力
    逐个确认 type=? 的仓库，把结论写进 data/type-overrides.json，重跑即生效。
    """
    topics = [t.lower() for t in (repo.topics or [])]

    # 1. 最高优先级：overrides（AI/人工确认过的映射）
    if overrides and repo.full_name.lower() in overrides:
        entry = overrides[repo.full_name.lower()]
        repo.type = entry.get("type") or "?"
        repo.form = entry.get("form")
    else:
        # 2. 兜底：description + topics（仓库自报信息，可信度较低）
        repo.type, repo.form = _match_type_form(repo.description.lower(), topics)

    # 附加 tag（基于 description，避免噪声）
    tags = []
    desc_lower = repo.description.lower()
    if "coding" in desc_lower or "code" in desc_lower:
        tags.append("coding")
    if "browser" in desc_lower:
        tags.append("browser")
    if "chinese" in desc_lower or "中文" in desc_lower:
        tags.append("cn")
    if repo.language:
        tags.append(f"lang:{repo.language.lower()}")
    repo.auto_tags = tags

    # review 标记 — 仅当 type 未知时才需要审查，form 可选不强制
    if repo.type == "?":
        repo.review_status = "needs_review"


# ---------------------------------------------------------------------------
# 一句话中文描述生成
# ---------------------------------------------------------------------------

# 已知重要项目的精准中文一句话描述（优先级最高）
CN_DESC_OVERRIDES = {
    "significant-gravitas/autogpt": "最早引爆 Agent 概念的自主任务执行框架，可自动拆解并完成目标",
    "torantulino/auto-gpt": "AutoGPT 早期仓库，自主 GPT Agent 的开山之作",
    "yoheinakajima/babyagi": "极简自主任务管理 Agent，任务驱动的自动化循环示范",
    "langchain-ai/langchain": "最流行的 LLM 应用与 Agent 开发框架，提供链式编排与工具调用",
    "langchain-ai/langgraph": "基于图的多 Agent 编排框架，支持有状态、可循环的工作流",
    "microsoft/autogen": "微软出品的多 Agent 对话协作框架",
    "crewaiinc/crewai": "面向角色分工的多 Agent 协作框架（Crew 概念）",
    "geekan/metagpt": "多 Agent 软件公司模拟框架，让 Agent 扮演不同角色协作开发",
    "microsoft/semantic-kernel": "微软的 LLM 编排 SDK，支持插件与规划器",
    "run-llama/llama_index": "面向 RAG 与数据检索的 LLM/Agent 框架",
    "camel-ai/camel": "多 Agent 交互与角色扮演的研究型框架",
    "openai/swarm": "OpenAI 官方的轻量多 Agent 编排实验框架",
    "openai/openai-agents-python": "OpenAI 官方 Python Agent SDK",
    "pydantic/pydantic-ai": "基于 Pydantic 的类型安全 Agent 框架",
    "cline/cline": "VSCode 内的自主编码 Agent 插件，可读写文件并执行命令",
    "roocodeinc/roo-code": "Cline 分支的 AI 编码 Agent 插件，功能更丰富",
    "continuedev/continue": "开源 AI 编码助手，支持 VSCode 与 JetBrains",
    "getcursor/cursor": "面向 AI 编程的 IDE（VSCode fork），Cursor 编辑器",
    "aider-ai/aider": "终端里的 AI 结对编程工具，直接改本地 Git 仓库",
    "openai/codex": "OpenAI 的命令行编码 Agent（Codex CLI）",
    "anthropics/claude-code": "Anthropic 官方命令行编码 Agent（Claude Code）",
    "sst/opencode": "开源的终端编码 Agent",
    "anomalyco/opencode": "开源的终端编码 Agent",
    "block/goose": "Block 出品的可扩展本地 AI Agent",
    "all-hands-ai/openhands": "开源的自主软件工程 Agent 平台（原 OpenDevin）",
    "openhands/openhands": "开源的自主软件工程 Agent 平台（原 OpenDevin）",
    "browser-use/browser-use": "让 AI Agent 操作浏览器、自动完成网页任务",
    "openinterpreter/open-interpreter": "在本地终端里让 LLM 执行代码的开源解释器",
    "assafelovic/gpt-researcher": "自主联网调研并生成报告的研究型 Agent",
    "google-gemini/gemini-cli": "谷歌 Gemini 的命令行 Agent 工具",
    "modelcontextprotocol/servers": "MCP（模型上下文协议）官方服务器集合",
    "n8n-io/n8n": "可视化工作流自动化平台，支持 AI 节点与 Agent 编排",
    "langgenius/dify": "开源 LLM 应用开发平台，支持 Agent 与工作流编排",
    "langflow-ai/langflow": "可视化拖拽式 LLM/Agent 流程搭建工具",
    "open-webui/open-webui": "自托管的 LLM 交互 Web 界面",
    "infiniflow/ragflow": "基于深度文档理解的开源 RAG 引擎",
    "lobehub/lobehub": "现代化开源 AI 聊天与助手框架",
    "reworkd/agentgpt": "浏览器里可视化配置与运行自主 Agent 的平台",
    "transformeroptimus/superagi": "面向开发者的自主 Agent 基础设施框架",
    "antonosika/gpt-engineer": "根据自然语言描述自动生成整个代码库的 Agent",
    "letta-ai/letta": "带长期记忆的有状态 Agent 框架（原 MemGPT）",
    "cpacker/memgpt": "为 LLM 引入长期记忆的 Agent 框架",
    "livekit/agents": "实时语音/多模态 Agent 构建框架",
    "khoj-ai/khoj": "可自托管的个人 AI 第二大脑与助手",
    "openbmb/xagent": "自主解决复杂任务的开源 Agent",
    "openclaw/openclaw": "开源个人 AI 助手，任意系统与平台均可部署（OpenClaw）",
    "nousresearch/hermes-agent": "Nous Research 出品的可自我成长的 Agent",
    "nextlevelbuilder/ui-ux-pro-max-skill": "为 AI 编码工具注入专业 UI/UX 设计智能的技能包",
    "earendil-works/pi": "一体化 Agent 工具箱：统一 LLM API、Agent 循环、TUI 与编码 CLI",
    "karpathy/autoresearch": "Karpathy 出品：AI Agent 自动执行单 GPU nanochat 训练研究",
    "egonex-ai/understand-anything": "把任意代码变成可探索、可检索、可问答的交互式知识图谱",
    "paperclipai/paperclip": "开源的团队 Agent 管理应用",
    "openbb-finance/openbb": "面向分析师、量化交易与 AI Agent 的开源金融数据平台",
    "code-yeongyu/oh-my-openagent": "面向复杂代码库的编码 Agent 工具集（支持 Codex / OpenCode）",
    "openinterpreter/openinterpreter": "面向开源模型（Kimi K3 等）的编码 Agent",
    "santifer/career-ops": "开源 AI 求职助手：扫描职位、结构化评分、定制简历、跟踪投递",
    "fission-ai/openspec": "面向 AI 编码助手的规格驱动开发（SDD）框架",
    "mintplex-labs/anything-llm": "本地优先的个人 AI 工作站，支持文档问答与 Agent 应用",
    "warpdotdev/warp": "从终端进化而来的 Agent 化开发环境",
    "aaif-goose/goose": "开源可扩展的 AI Agent CLI：安装、执行、编辑、测试一体",
    "cherryhq/cherry-studio": "多模型 AI 工作台：智能对话、自主 Agent 与 300+ 助手",
    "stablyai/orca": "并行调度成群编码 Agent 的 Agent 开发环境（ADE）",
    "calesthio/openmontage": "开源 Agent 化视频生产系统：12 条管线、100+ 工具",
    "hkuds/cli-anything": "让所有软件 Agent 原生化的 CLI 中心",
    "chatboxai/chatbox": "跨平台桌面 AI 客户端，支持多家模型服务",
    "copilotkit/copilotkit": "面向 Agent 与生成式 UI 的前端框架（AG-UI 协议出品方）",
    "agentscope-ai/qwenpaw": "易装易部署的个人 AI 助手，本地/云端皆可，支持多聊天应用接入",
    "tabbyml/tabby": "可自托管的 AI 编码助手（代码补全）",
    "pythagora-io/gpt-pilot": "从需求到成品全程自主开发的 AI 开发者（GPT Pilot）",
    "zeroclaw-labs/zeroclaw": "轻量快速的自主个人 AI 助手基础设施，任意平台可部署",
    "iofficeai/officecli": "为 AI Agent 打造的 Office 命令行套件：读写并自动化 Word/Excel/PPT",
    "qwenlm/qwen-code": "阿里 Qwen 团队出品的终端编码 Agent",
    "rohitg00/agentmemory": "基于真实基准评测的编码 Agent 持久化记忆方案",
    "kilo-org/kilocode": "一体化 Agent 化工程平台，内置热门开源编码 Agent",
    "othmanadi/planning-with-files": "为编码 Agent 提供基于文件的持久化规划，防崩溃、可恢复",
    "decolua/9router": "免费 AI 编码路由：40+ 渠道接入 Claude/GPT/Gemini，自动降级切换",
    "mksglu/context-mode": "编码 Agent 上下文优化：沙箱化工具输出、持久会话记忆、智能路由",
    "stitionai/devika": "开源 Agentic 软件工程师，Devin 的开源替代",
    "leon-ai/leon": "开源个人 AI 助手，本地部署保护隐私",
    "larksuite/cli": "飞书官方 CLI：覆盖 IM/文档/表格/日历等，内置 200+ 命令与 Agent 技能",
    "open-metadata/openmetadata": "面向数据与 AI 的开源元数据平台，为人和 Agent 提供可信数据上下文",
    "obra/superpowers": "实用的 Agent 技能框架与软件开发方法论",
    "mattpocock/skills": "工程师向的 Agent 技能合集（.agents 目录直出）",
    "anthropics/skills": "Anthropic 官方 Agent Skills 公共仓库",
    "msitarzewski/agency-agents": "一整套 AI 代理公司 Agent 合集：各司其职的专家型 Agent",
    "github/spec-kit": "GitHub 官方规格驱动开发（Spec-Driven Development）工具包",
    "farion1231/cc-switch": "跨平台桌面工具：一站式切换管理 Claude Code、Codex、OpenCode 等编码 Agent",
    "graphify-labs/graphify": "把代码库、文档、SQL、PDF 变成可查询知识图谱的编码 Agent 技能",
    "dietrichgebert/ponytail": "让 AI Agent 像最懒资深工程师一样思考：能不写的代码就不写",
    "thedotmack/claude-mem": "为编码 Agent 提供跨会话持久记忆，自动压缩并注入历史上下文",
    "koala73/worldmonitor": "实时全球情报仪表盘：AI 新闻聚合与地缘政治监控",
    "leonxlnx/taste-skill": "提升 AI 审美的技能包，避免生成千篇一律的内容",
    "shareai-lab/learn-claude-code": "从 0 到 1 构建的迷你 Claude Code 式 Agent 框架教学项目",
    "panniantong/agent-reach": "给 AI Agent 装上眼睛：一个 CLI 读取全网内容，零 API 费用",
    "daytonaio/daytona": "运行 AI 生成代码的安全弹性沙箱基础设施",
    "colbymchenry/codegraph": "本地预索引代码知识图谱，随代码变更自动同步，为编码 Agent 省 token",
}

EN_DESC_OVERRIDES = {
    "significant-gravitas/autogpt": "The breakout autonomous task execution framework that popularized the Agent concept.",
    "torantulino/auto-gpt": "An early AutoGPT repository and one of the first widely known autonomous GPT agents.",
    "yoheinakajima/babyagi": "A minimal autonomous task management agent built around iterative task execution.",
    "langchain-ai/langchain": "The most widely used LLM app and agent framework for chains, tools, and orchestration.",
    "langchain-ai/langgraph": "A graph-based framework for stateful, multi-agent workflows with loops and control flow.",
    "microsoft/autogen": "Microsoft's multi-agent conversation and collaboration framework.",
    "crewaiinc/crewai": "A role-based multi-agent collaboration framework built around the Crew abstraction.",
    "geekan/metagpt": "A multi-agent software company simulation framework with role-based collaboration.",
    "microsoft/semantic-kernel": "Microsoft's LLM orchestration SDK with plugins, planning, and agent capabilities.",
    "run-llama/llama_index": "An LLM and agent framework focused on retrieval, data connectors, and RAG workflows.",
    "camel-ai/camel": "A research-oriented framework for multi-agent interaction and role-playing.",
    "openai/swarm": "OpenAI's lightweight experimental framework for multi-agent orchestration.",
    "openai/openai-agents-python": "OpenAI's official Python SDK for building agents.",
    "pydantic/pydantic-ai": "A type-safe agent framework built on top of Pydantic.",
    "cline/cline": "An autonomous coding agent extension for VS Code that can edit files and run commands.",
    "roocodeinc/roo-code": "A feature-rich AI coding agent extension derived from Cline.",
    "continuedev/continue": "An open-source AI coding assistant for VS Code and JetBrains.",
    "getcursor/cursor": "An AI-first IDE based on a VS Code fork.",
    "aider-ai/aider": "A terminal-based AI pair programming tool that edits your local Git repo.",
    "openai/codex": "OpenAI's command-line coding agent experience.",
    "anthropics/claude-code": "Anthropic's official command-line coding agent.",
    "sst/opencode": "An open-source coding agent for the terminal.",
    "anomalyco/opencode": "An open-source coding agent for the terminal.",
    "block/goose": "A locally extensible AI agent from Block.",
    "all-hands-ai/openhands": "An open-source autonomous software engineering agent platform formerly known as OpenDevin.",
    "openhands/openhands": "An open-source autonomous software engineering agent platform formerly known as OpenDevin.",
    "browser-use/browser-use": "A framework that lets AI agents operate browsers and complete web tasks.",
    "openinterpreter/open-interpreter": "An open-source local interpreter that lets LLMs execute code from the terminal.",
    "assafelovic/gpt-researcher": "A research agent that browses the web and generates reports.",
    "google-gemini/gemini-cli": "Google Gemini's command-line agent experience.",
    "modelcontextprotocol/servers": "The official server collection for the Model Context Protocol ecosystem.",
    "n8n-io/n8n": "A visual workflow automation platform with AI nodes and agent orchestration support.",
    "langgenius/dify": "An open-source platform for building LLM apps, workflows, and agents.",
    "langflow-ai/langflow": "A visual drag-and-drop builder for LLM and agent workflows.",
    "open-webui/open-webui": "A self-hosted web UI for interacting with LLMs.",
    "infiniflow/ragflow": "An open-source RAG engine focused on deep document understanding.",
    "lobehub/lobehub": "A modern open-source chat and assistant framework for AI applications.",
    "reworkd/agentgpt": "A browser-based platform for configuring and running autonomous agents.",
    "transformeroptimus/superagi": "An autonomous agent infrastructure framework for developers.",
    "antonosika/gpt-engineer": "An agent that generates codebases from natural language specifications.",
    "letta-ai/letta": "A stateful agent framework with long-term memory, formerly known as MemGPT.",
    "cpacker/memgpt": "An agent framework that brings long-term memory to LLMs.",
    "livekit/agents": "A framework for building real-time voice and multimodal agents.",
    "khoj-ai/khoj": "A self-hostable personal AI assistant and second brain.",
    "openbmb/xagent": "An open-source agent designed to solve complex tasks autonomously.",
}

# 术语替换词典（按长短语优先匹配），用于兜底翻译
CN_TERM_MAP = [
    ("ai software engineer", "AI 软件工程师"),
    ("ai pair programmer", "AI 结对编程助手"),
    ("coding assistant", "编码助手"),
    ("code assistant", "编码助手"),
    ("coding agent", "编码 Agent"),
    ("autonomous agent", "自主 Agent"),
    ("multi-agent framework", "多 Agent 框架"),
    ("multi-agent", "多 Agent"),
    ("browser automation", "浏览器自动化"),
    ("browser agent", "浏览器 Agent"),
    ("model context protocol", "模型上下文协议（MCP）"),
    ("mcp servers", "MCP 服务器"),
    ("mcp server", "MCP 服务器"),
    ("workflow automation", "工作流自动化"),
    ("self-hosted", "自托管"),
    ("open-source", "开源"),
    ("open source", "开源"),
]


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def clean_desc_text(text: str) -> str:
    """Normalize raw descriptions into a cleaner single line."""
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", (text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


FORM_CN_LABELS = {
    "IDE-plugin": "Agent IDE 插件",
    "AI-IDE": "AI 原生 IDE",
    "CLI": "命令行 Agent 工具",
    "Web-Builder": "网页搭建 Agent",
    "Cloud-General": "云端通用 Agent",
    "Multi-Agent-Framework": "多 Agent 框架",
    "Browser-Agent": "浏览器 Agent",
    "MCP-Ecosystem": "MCP 生态项目",
    "Self-Hosted": "可自托管项目",
}


def make_cn_desc(repo: Repo) -> str:
    """为仓库生成一句话中文描述。

    优先级：
      1. 命中 CN_DESC_OVERRIDES → 精准中文
      2. 原描述本身含中文 → 直接用原描述
      3. 根据核心特征生成一句模板化中文概括（保留英文原文作参考）
      4. 其余 → 直接保留英文原文（不做逐词替换，避免中英混杂难读）
    """
    key = repo.full_name.lower()
    if key in CN_DESC_OVERRIDES:
        return CN_DESC_OVERRIDES[key]

    desc = (repo.description or "").strip()
    if not desc:
        return "（无描述）"

    # 已经含中文，直接用
    if has_cjk(desc):
        return desc

    cleaned = clean_desc_text(desc)
    low = cleaned.lower()

    # 基于关键特征生成模板化中文概括（整句归纳，不逐词替换）
    summary = None
    if "coding agent" in low or ("coding" in low and "agent" in low):
        summary = "编码 Agent 相关项目"
    elif "mcp server" in low or "model context protocol" in low:
        summary = "MCP（模型上下文协议）相关项目"
    elif "multi-agent" in low or "multi agent" in low:
        summary = "多 Agent 框架 / 应用"
    elif "browser" in low and "agent" in low:
        summary = "浏览器操作类 Agent"
    elif "framework" in low and "agent" in low:
        summary = "Agent 开发框架"
    elif "coding assistant" in low or "code assistant" in low:
        summary = "AI 编码助手"
    elif "research" in low and "agent" in low:
        summary = "研究型 / 调研类 Agent"
    elif "autonomous" in low:
        summary = "自主执行类 Agent"
    elif "skill" in low and "agent" in low:
        summary = "Agent 技能 / 能力扩展"
    elif "agent" in low:
        summary = "Agent 相关项目"

    if summary:
        return f"{summary}（{cleaned}）"

    # 兜底：按 form/type 生成中文概括前缀，保留英文原文作参考。
    # 不再直接返回英文原文，避免中文榜单出现整句英文描述。
    form_cn = FORM_CN_LABELS.get(repo.form or "")
    if form_cn:
        return f"{form_cn}（{cleaned}）"
    return f"Agent 相关项目（{cleaned}）"


TYPE_EN_LABELS = {
    "Product": "product",
    "Framework": "framework",
    "Middleware": "middleware layer",
    "?": "project",
}

FORM_EN_LABELS = {
    "IDE-plugin": "IDE plugin",
    "AI-IDE": "AI-first IDE",
    "CLI": "CLI tool",
    "Web-Builder": "web builder",
    "Cloud-General": "cloud agent",
    "Multi-Agent-Framework": "multi-agent framework",
    "Browser-Agent": "browser agent",
    "MCP-Ecosystem": "MCP project",
    "Self-Hosted": "self-hosted project",
}


def make_en_desc(repo: Repo) -> str:
    """Generate a concise English one-liner for the repository."""
    key = repo.full_name.lower()
    if key in EN_DESC_OVERRIDES:
        return EN_DESC_OVERRIDES[key]

    desc = (repo.description or "").strip()
    if not desc:
        base = FORM_EN_LABELS.get(repo.form or "", "")
        kind = TYPE_EN_LABELS.get(repo.type, "project")
        if base:
            return f"An Agent ecosystem {base.lower()}."
        return f"An Agent-related {kind}."

    cleaned = clean_desc_text(desc)
    if not has_cjk(cleaned):
        return cleaned

    form = FORM_EN_LABELS.get(repo.form or "", "")
    kind = TYPE_EN_LABELS.get(repo.type, "project")
    if form:
        return f"An Agent-related {form.lower()} in the {kind} category."
    return f"An Agent-related {kind}."


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def sort_by_stars(repos: list[Repo]) -> list[Repo]:
    return sorted(repos, key=lambda r: (-r.stars, r.full_name.lower()))


def format_pushed(dt_str: str) -> str:
    """把 ISO 时间格式化为更友好的形式"""
    d = parse_iso_datetime(dt_str)
    if not d:
        return dt_str
    return d.strftime("%Y-%m-%d")


def output_text(lang: str) -> dict[str, str]:
    if lang == "en":
        return {
            "snapshot_label": "Data Snapshot",
            "note_label": "Notes",
            "project_col": "Project",
            "type_col": "Type",
            "form_col": "Form",
            "last_push_col": "Last Push",
            "descriptions_heading": "### One-line Descriptions",
            "review_title": "# Review Queue",
            "review_count_prefix": "There are",
            "review_count_suffix": "candidate repos that need manual review.",
            "review_intro": "For each item, please decide:",
            "review_question_1": "1. Should it be included? (Is it a real Agent project?)",
            "review_question_2": "2. If yes, what should its type and form be?",
            "description_label": "Description",
            "discovery_label": "Discovery",
            "no_desc": "*(No description)*",
            "bilingual_heading": "### One-line Descriptions (Chinese / English)",
            "data_snapshot_note": "Data snapshot",
        }
    if lang == "bilingual":
        return {
            "snapshot_label": "Data Snapshot / 数据快照",
            "note_label": "Notes / 说明",
            "project_col": "Project / 项目",
            "type_col": "Type / 类型",
            "form_col": "Form / 形态",
            "last_push_col": "Last Push / 最近提交",
            "descriptions_heading": "### One-line Descriptions / 一句话描述",
            "review_title": "# Review Queue / 人审队列",
            "review_count_prefix": "There are / 共",
            "review_count_suffix": "candidate repos that need manual review / 条候选记录需要人工判定。",
            "review_intro": "For each item, please decide / 对每一条，请判断：",
            "review_question_1": "1. Should it be included? / 是否应该收录？（是否是真正的 Agent 项目）",
            "review_question_2": "2. If yes, what should its type and form be? / 如果收录，type 应该是什么？form 应该是什么？",
            "description_label": "Description / 描述",
            "discovery_label": "Discovery / 发现来源",
            "no_desc": "*(无描述 / No description)*",
            "bilingual_heading": "### One-line Descriptions (Chinese / English)",
            "data_snapshot_note": "Data snapshot / 数据快照",
        }
    return {
        "snapshot_label": "数据快照",
        "note_label": "说明",
        "project_col": "项目",
        "type_col": "类型",
        "form_col": "形态",
        "last_push_col": "最近提交",
        "descriptions_heading": "### 一句话描述",
        "review_title": "# 人审队列",
        "review_count_prefix": "共",
        "review_count_suffix": "条候选记录需要人工判定。",
        "review_intro": "对每一条，请判断：",
        "review_question_1": "1. 是否应该收录？（是否是真正的 Agent 项目）",
        "review_question_2": "2. 如果收录，type 应该是什么？form 应该是什么？",
        "description_label": "Description",
        "discovery_label": "Discovery",
        "no_desc": "*(无描述)*",
        "bilingual_heading": "### 一句话描述（中文 / English）",
        "data_snapshot_note": "数据快照",
    }


def to_markdown_table(repos: list[Repo], title: str, note: str, snapshot: str, lang: str) -> str:
    t = output_text(lang)
    lines = [f"# {title}", ""]
    lines.append(f"> **{t['snapshot_label']}**: {snapshot}")
    lines.append(f"> **{t['note_label']}**: {note}")
    lines.append("")
    lines.append(
        f"| # | {t['project_col']} | Stars | Forks | {t['type_col']} | {t['form_col']} | Language | License | {t['last_push_col']} |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(repos, 1):
        link = f"[{r.full_name}]({r.url})"
        lines.append(
            f"| {i} | {link} | {r.stars:,} | {r.forks:,} | {r.type} | {r.form or '?'} | {r.language or '-'} | {r.license or '-'} | {format_pushed(r.pushed_at)} |"
        )
    lines.append("")
    lines.append("---")
    if lang == "bilingual":
        lines.append(t["bilingual_heading"])
    else:
        lines.append(t["descriptions_heading"])
    lines.append("")
    for i, r in enumerate(repos, 1):
        desc_cn = r.desc_cn or t["no_desc"]
        desc_en = r.desc_en or r.description or "*(No description)*"
        lines.append(f"{i}. **{r.full_name}**")
        if lang == "zh":
            lines.append(f"   - {desc_cn}")
        elif lang == "en":
            lines.append(f"   - {desc_en}")
        else:
            lines.append(f"   - CN: {desc_cn}")
            lines.append(f"   - EN: {desc_en}")
    return "\n".join(lines)


def to_csv(repos: list[Repo], path: Path) -> None:
    fields = [
        "rank",
        "full_name",
        "stars",
        "forks",
        "type",
        "form",
        "language",
        "license",
        "pushed_at",
        "is_active",
        "days_since_push",
        "url",
        "description",
        "desc_cn",
        "desc_en",
        "topics",
        "auto_tags",
        "discovery_source",
        "review_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for i, r in enumerate(repos, 1):
            w.writerow(
                [
                    i,
                    r.full_name,
                    r.stars,
                    r.forks,
                    r.type,
                    r.form or "",
                    r.language or "",
                    r.license or "",
                    r.pushed_at,
                    r.is_active,
                    r.days_since_push,
                    r.url,
                    r.description,
                    r.desc_cn,
                    r.desc_en,
                    "|".join(r.topics),
                    "|".join(r.auto_tags),
                    "|".join(r.discovery_source),
                    r.review_status,
                ]
            )


def write_review_queue(repos: list[Repo], path: Path, lang: str) -> None:
    t = output_text(lang)
    needs_review = [r for r in repos if r.review_status == "needs_review"]
    lines = [t["review_title"], ""]
    lines.append(f"{t['review_count_prefix']} {len(needs_review)} {t['review_count_suffix']}")
    lines.append("")
    lines.append(t["review_intro"])
    lines.append(t["review_question_1"])
    lines.append(t["review_question_2"])
    lines.append("")
    for r in needs_review:
        lines.append(f"### [{r.full_name}]({r.url})")
        lines.append(f"- Stars: {r.stars:,}  |  Language: {r.language or '-'}  |  Pushed: {format_pushed(r.pushed_at)}")
        lines.append(f"- {t['description_label']}: {r.description or t['no_desc']}")
        lines.append(f"- Auto-detected type: `{r.type}`, form: `{r.form or '?'}`")
        lines.append(f"- {t['discovery_label']}: {', '.join(r.discovery_source)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=50, help="Active ranking size / 主榜取多少项（默认 50）")
    parser.add_argument("--legacy", type=int, default=20, help="Legacy ranking size / 历史榜取多少项（默认 20）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory / 输出目录")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Config directory / 配置文件目录")
    parser.add_argument("--lang", choices=["zh", "en", "bilingual"], default="bilingual", help="Output language / 输出语言：zh / en / bilingual")
    parser.add_argument("--quiet", action="store_true", help="Reduce logs / 减少日志")
    args = parser.parse_args()

    verbose = not args.quiet
    args.output_dir.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    # Startup status banner / 启动状态提示
    print("=" * 60, file=sys.stderr)
    if token:
        print("✅ GitHub Token loaded / 已加载", file=sys.stderr)
        print(f"   Rate limit: /search 30/min, /repos 5000/hour", file=sys.stderr)
    else:
        print("⚠️  GITHUB_TOKEN not detected / 未检测到 GITHUB_TOKEN", file=sys.stderr)
        print("", file=sys.stderr)
        print("   Rate limit (unauthenticated) / 未登录限流: /search 10/min, /repos 60/hour", file=sys.stderr)
        print("   This run needs ~5000 /repos requests and likely cannot finish unauthenticated. / 本次跑批预计需要 ~5000 次 /repos 请求，未登录基本跑不完。", file=sys.stderr)
        print("", file=sys.stderr)
        print("   👉 Recommended setup (pick one) / 推荐配置（选一种）：", file=sys.stderr)
        print(f"      A. Create a .env under {SCRIPT_DIR}/ with / 在 {SCRIPT_DIR}/ 下创建 .env，内容：", file=sys.stderr)
        print("         GITHUB_TOKEN=ghp_your_token_here", file=sys.stderr)
        print("      B. Temporary export / 临时 export：", file=sys.stderr)
        print("         export GITHUB_TOKEN=ghp_your_token_here", file=sys.stderr)
        print("      Create a token / Token 创建：https://github.com/settings/tokens", file=sys.stderr)
        print("      Only the public_repo scope is needed / 仅需勾选 public_repo 权限。", file=sys.stderr)
        print("", file=sys.stderr)

        # Give the user a chance to cancel / 给用户 5 秒机会取消
        try:
            print("   ⏸  Press Ctrl+C within 5s to cancel, or wait to start a degraded run... / 5 秒内按 Ctrl+C 可取消；或等待自动开始降级跑批...", file=sys.stderr)
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nCancelled. Configure GITHUB_TOKEN and retry. / 已取消。请配置 GITHUB_TOKEN 后重试。", file=sys.stderr)
            return 130
    print("=" * 60, file=sys.stderr)

    # 初始化全局 opener（识别代理环境变量）
    global _OPENER
    _OPENER = build_url_opener()

    gh = GitHubClient(token=token, verbose=verbose)

    # 1. Load config / 加载配置
    print("📂 Loading config... / 加载配置...", file=sys.stderr)
    cfg = load_config(args.data_dir)

    # 2. Three discovery paths / 三路发现
    print("🔎 [Step 1/6] Keyword search / 关键词搜索发现...", file=sys.stderr)
    pool = discover_via_keywords(gh, cfg["keywords"]["keywords"])

    print("📋 [Step 2/6] Seed list / Seed list 强制收录...", file=sys.stderr)
    seed_pool = discover_via_seed(gh, cfg["seed"])
    seed_keys = set(seed_pool.keys())
    for key, data in seed_pool.items():
        if key in pool:
            pool[key].setdefault("_sources", []).extend(data.get("_sources", []))
        else:
            pool[key] = data

    print("📚 [Step 3/6] Awesome lists / Awesome 列表抽取...", file=sys.stderr)
    awesome_urls = discover_via_awesome(gh, cfg["awesome"])
    enrich_awesome_urls(gh, awesome_urls, pool)

    print(f"✅ Candidate pool: {len(pool)} repos / 候选池共 {len(pool)} 个仓库", file=sys.stderr)

    # 3. Filtering / 过滤
    print("🚫 [Step 4/6] Apply exclusion rules / 应用排除规则...", file=sys.stderr)
    kept: list[dict] = []
    excluded = []
    for key, raw in pool.items():
        should, reason = should_exclude(raw, cfg["exclude"], seed_keys)
        if should:
            excluded.append((key, reason))
        else:
            kept.append(raw)
    print(f"  → Kept {len(kept)}, excluded {len(excluded)} / 保留 {len(kept)} 个，排除 {len(excluded)} 个", file=sys.stderr)

    # 4. Normalize / 归一化
    print("🧹 [Step 5/6] Normalize + tag / 归一化 + 打标...", file=sys.stderr)
    repos = [normalize(r) for r in kept]

    # 5. Tagging / 打标（type-overrides.json 优先，description/topics 兜底）
    for r in repos:
        classify(r, overrides=cfg.get("type_overrides"))

    # Generate one-line descriptions / 生成一句话描述
    for r in repos:
        r.desc_cn = make_cn_desc(r)
        r.desc_en = make_en_desc(r)

    # 6. Generate rankings / 生成榜单
    print("📊 [Step 6/6] Generate rankings / 生成榜单...", file=sys.stderr)
    active_repos = [r for r in repos if r.is_active]
    legacy_repos = [r for r in repos if not r.is_active]

    top_active = sort_by_stars(active_repos)[: args.top]
    top_legacy = sort_by_stars(legacy_repos)[: args.legacy]
    top_products = sort_by_stars([r for r in repos if r.type == "Product"])[: args.top]

    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    active_filename = active_ranking_filename(len(top_active), date_str)
    legacy_filename = legacy_ranking_filename(len(top_legacy), date_str)
    products_filename = product_ranking_filename(len(top_products), date_str)
    snapshot_filename = f"snapshot-{date_str}.json"
    metadata_filename = f"metadata-{date_str}.json"
    csv_filename = f"ranking-all-{date_str}.csv"
    review_queue_filename = f"review-queue-{date_str}.md"
    if args.lang == "en":
        active_title = f"Active GitHub Agent Ecosystem Top {len(top_active)}"
        active_note = f"Filter: pushed within the last {ACTIVE_DAYS_THRESHOLD} days; ranked by stars"
        legacy_title = f"Legacy GitHub Agent Ecosystem Top {len(top_legacy)}"
        legacy_note = f"Only includes repos with no push activity for more than {ACTIVE_DAYS_THRESHOLD} days"
        products_title = f"GitHub Agent Products Top {len(top_products)}"
        products_note = "Only repos classified as Product (end-user agent products); ranked by stars"
    elif args.lang == "bilingual":
        active_title = f"主榜 · GitHub Agent 生态活跃 Top {len(top_active)} / Active GitHub Agent Ecosystem Top {len(top_active)}"
        active_note = f"过滤条件：最近 {ACTIVE_DAYS_THRESHOLD} 天内有 push；按 star 排序 / Filter: pushed within the last {ACTIVE_DAYS_THRESHOLD} days; ranked by stars"
        legacy_title = f"副榜 1 · GitHub Agent 历史 Top {len(top_legacy)}（含已停维护） / Legacy GitHub Agent Ecosystem Top {len(top_legacy)}"
        legacy_note = f"仅保留 pushed_at 已超过 {ACTIVE_DAYS_THRESHOLD} 天的仓库 / Only includes repos with no push activity for more than {ACTIVE_DAYS_THRESHOLD} days"
        products_title = f"副榜 2 · GitHub Agent 纯产品 Top {len(top_products)} / GitHub Agent Products Top {len(top_products)}"
        products_note = f"仅收录 type=Product 的终端可用 Agent 产品（不含框架/中间件）；按 star 排序 / Only repos classified as Product (end-user agent products, no frameworks/middleware); ranked by stars"
    else:
        active_title = f"主榜 · GitHub Agent 生态活跃 Top {len(top_active)}"
        active_note = f"过滤条件：最近 {ACTIVE_DAYS_THRESHOLD} 天内有 push；按 star 排序"
        legacy_title = f"副榜 1 · GitHub Agent 历史 Top {len(top_legacy)}（含已停维护）"
        legacy_note = f"仅保留 pushed_at 已超过 {ACTIVE_DAYS_THRESHOLD} 天的仓库"
        products_title = f"副榜 2 · GitHub Agent 纯产品 Top {len(top_products)}"
        products_note = "仅收录 type=Product 的终端可用 Agent 产品（不含框架/中间件）；按 star 排序"

    # 输出
    out = args.output_dir
    (out / active_filename).write_text(
        to_markdown_table(
            top_active,
            active_title,
            active_note,
            snapshot,
            args.lang,
        ),
        encoding="utf-8",
    )

    (out / legacy_filename).write_text(
        to_markdown_table(
            top_legacy,
            legacy_title,
            legacy_note,
            snapshot,
            args.lang,
        ),
        encoding="utf-8",
    )

    (out / products_filename).write_text(
        to_markdown_table(
            top_products,
            products_title,
            products_note,
            snapshot,
            args.lang,
        ),
        encoding="utf-8",
    )

    (out / snapshot_filename).write_text(
        json.dumps(
            {
                "snapshot_date": snapshot,
                "total_candidates": len(pool),
                "kept": len(repos),
                "excluded": [{"key": k, "reason": r} for k, r in excluded],
                "active_top": [asdict(r) for r in top_active],
                "legacy_top": [asdict(r) for r in top_legacy],
                "products_top": [asdict(r) for r in top_products],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (out / metadata_filename).write_text(
        json.dumps(
            {
                "snapshot_date": snapshot,
                "top": args.top,
                "legacy": args.legacy,
                "lang": args.lang,
                "output_dir": str(out),
                "data_dir": str(args.data_dir),
                "active_output": active_filename,
                "legacy_output": legacy_filename,
                "products_output": products_filename,
                "csv_output": csv_filename,
                "snapshot_output": snapshot_filename,
                "review_queue_output": review_queue_filename,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # CSV
    to_csv(repos, out / csv_filename)

    # 人审队列
    write_review_queue(sort_by_stars(repos)[: args.top + args.legacy], out / review_queue_filename, args.lang)

    print(f"\n✅ Done! Output directory / 完成！输出目录：{out}", file=sys.stderr)
    print(f"   · Active: {active_filename} ({len(top_active)} items / 项)", file=sys.stderr)
    print(f"   · Legacy: {legacy_filename} ({len(top_legacy)} items / 项)", file=sys.stderr)
    print(f"   · Products: {products_filename} ({len(top_products)} items / 项)", file=sys.stderr)
    print(f"   · Full data: {snapshot_filename} / {metadata_filename} / {csv_filename} / 完整数据", file=sys.stderr)
    print(f"   · Review queue: {review_queue_filename} / 人审队列", file=sys.stderr)
    print(f"\n📅 Data snapshot / 数据快照: {snapshot}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
