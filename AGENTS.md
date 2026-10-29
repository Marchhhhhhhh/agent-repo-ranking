# AGENTS.md — Agent Working Rules

Instructions for AI agents (Claude Code, Cursor, WorkBuddy, etc.) working in this repository.

## Security

- **NEVER commit secrets.** `.env` contains a real `GITHUB_TOKEN`. Before any `git commit`, always inspect `git diff --cached` — if any token, key, or credential appears in staged changes, unstage it immediately (`git reset HEAD <file>`) and warn the user.
- `.env` is listed in `.gitignore` but is still tracked by git (it was committed with a placeholder before). Pending fix: `git rm --cached .env` (requires user confirmation, since it changes what collaborators receive).
- Never print or log token values, even partially, in commit messages, logs, or generated files.

## Git Conventions

- **Commit messages must be in English.** Use conventional-commit style: `feat:`, `fix:`, `docs:`, `chore:`, `data:` etc., followed by an imperative summary line (≤72 chars). Body (optional) explains what and why, also in English.
- Example: `fix: catch IncompleteRead in retry loop to survive truncated responses`
- Re-run `python3 search_agents.py` after editing `data/*.json` so outputs reflect the change, and commit configs + regenerated outputs together when both changed.

## Trigger: "获取最新排名" (Get Latest Ranking)

When the user says **"获取最新排名"** (or an unambiguous English equivalent such as "get the latest ranking" / "refresh the rankings"), execute this workflow end to end without asking for confirmation:

1. **Run the collector.** Fetch a fresh snapshot:
   ```bash
   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY python3 search_agents.py
   ```
   The run hits the GitHub API for thousands of repos, so it can take several minutes — if your shell has a short timeout, run it in the background and redirect output to a log file, then read the log. Confirm a new dated snapshot was written to `output/` (`ranking-all-<date>.csv`, `snapshot-<date>.json`, `ranking-active-top50-<date>.md`, `ranking-products-top50-<date>.md`, plus review-queue/verification/metadata files). If the run fails or a dated output is missing, stop and report the error — do not hand-write outputs.

2. **Regenerate the root ranking files.** Run `python3 generate_ranking_md.py`. It auto-detects the two most recent snapshots and rewrites `ranking-en.md` / `ranking-zh.md` with Active Top 50, Products Top 50, **Recently Surging / 近期飙升** and **Newly Added / 新收录**. Verify both files now show the new snapshot timestamp, 100 table rows (2 × 50), and 50 description items; never edit these files by hand.

3. **Commit and push.** `output/` is gitignored, so the commit normally contains only `ranking-en.md` / `ranking-zh.md` (plus any script or `data/*.json` changes). Stage them, inspect `git diff --cached` for secrets (see Security above), then commit with an English conventional message (e.g. `docs: refresh rankings with <YYYY-MM-DD> snapshot`) and push to the remote:
   ```bash
   git push origin main
   ```
   Report the final commit hash, the snapshot timestamp, and the headline movers (top surging / newly added repos) back to the user.

Optional follow-ups (do them only if the user asks, or if obviously needed): review `output/review-queue-<date>.md` and record conclusions in `data/type-overrides.json` / `data/exclude-patterns.json`, then repeat steps 1–3.

## Project Workflow

- This repo generates ranking snapshots of the GitHub Agent ecosystem. Core script: `search_agents.py` (zero-dependency, stdlib only — keep it that way).
- Correction loop: run script → review `output/review-queue-*.md` → write conclusions into `data/type-overrides.json` (include) or `data/exclude-patterns.json` (exclude) → re-run.
- Outputs in `output/` are dated snapshots; never edit them by hand — regenerate via the script.
- When running the script from an agent sandbox, clear proxy env vars first (`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY`) to avoid truncated responses.

## Updating ranking-en.md / ranking-zh.md

- After each new snapshot, regenerate the root ranking files with `python3 generate_ranking_md.py` (zero-dependency, stdlib only — keep it that way). Never edit `ranking-en.md` / `ranking-zh.md` by hand.
- The generator auto-detects the two most recent `output/ranking-all-*.csv` snapshots and renders: Active Top 50, Products Top 50, a **Recently Surging / 近期飙升** section (biggest star gains vs the previous snapshot), a **Newly Added / 新收录** subsection (repos entering the dataset for the first time), and one-line descriptions.
- The surging/newly-added sections are a required part of the ranking files — always include them when refreshing rankings. Thresholds (min gain/stars, top-N) live at the top of `generate_ranking_md.py`.
- Commit the regenerated `ranking-en.md` / `ranking-zh.md` together with any script changes.
