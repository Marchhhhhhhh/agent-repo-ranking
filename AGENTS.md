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

## Project Workflow

- This repo generates ranking snapshots of the GitHub Agent ecosystem. Core script: `search_agents.py` (zero-dependency, stdlib only — keep it that way).
- Correction loop: run script → review `output/review-queue-*.md` → write conclusions into `data/type-overrides.json` (include) or `data/exclude-patterns.json` (exclude) → re-run.
- Outputs in `output/` are dated snapshots; never edit them by hand — regenerate via the script.
- When running the script from an agent sandbox, clear proxy env vars first (`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY`) to avoid truncated responses.
