---
id: 21
slug: template-upgrade
status: active
branch: feature/template-upgrade
created: 2026-10-18T17:33:15-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/70
---

# Upgrade to proj-template v0.11.2

## Plan

Bring the repo from proj-template v0.10.0 to **v0.11.2** with the template's
`install-template` skill (upgrade mode), read from the `v0.11.2` tag, never from
the template's working tree.

### Classification

**package**: hatchling `[build-system]` and a `publish.yml`.

### What the release changes here

This is the v0.10.0..v0.11.2 template delta, applied file by file; every
other template-managed file here still matches v0.10.0, so nothing else moves:

- `pyproject.toml`: the dev-group ruff pin gains the template's `<0.17` cap (same
  minor as the pre-commit rev), keeping its `>=0.16.4` floor; `[tool.ruff]` gains
  `extend-exclude = ["*.md"]`. Edited by script; then
  `uv lock --upgrade-package ruff`.
- `.pre-commit-config.yaml`: `default_stages: [pre-commit]`, `ruff` hook id to
  `ruff-check`, `planners validate` run through `uv run`, the post-merge
  `planners-index` hook (already present; its entry moves to `uv run`), and the `ruff-pre-commit` rev set to the locked ruff.
- `.gitattributes`: already carries the template's entry; no change.
- `test.yml` and `publish.yml`: setup-uv to the template's v10.2.0 pin.
- `.claude/hooks/lint-typecheck.sh`: the v0.11.2 copy (the uv-workspace root
  rule). `.claude/` is gitignored, so the hook is copied into the main checkout after the merge, not in the branch.
- `[tool.proj-template] version = "0.11.2"`, written last.

### What stays

The full CI matrix, the coverage floor (100), and the Dependabot config. `.claude/settings.json` is untouched by the delta. `renovate.yml` is
not added: the repo uses Dependabot.

### Checks

`uv sync --all-groups`; `ruff check` and `ruff format --check`; `pyrefly check`;
`pre-commit install --hook-type pre-commit --hook-type post-merge` and
`pre-commit run --all-files`; `pytest` with its coverage gate. Then re-install
the git hooks from the main checkout so none points into `.worktrees/`.

## Log

- **2026-10-18T17:33:53-07:00** — Activated.
  - Branch: `feature/template-upgrade`
  - Base: `dev` at `5cebc22`
  - Worktree: `.worktrees/template-upgrade`
  - PR: pending
- **2026-10-18T17:40:53-07:00** — Applied the v0.10.0..v0.11.2 delta in the worktree; all checks green (ruff, pyrefly, pre-commit --all-files, pytest with coverage). Locked ruff 0.16.10, pre-commit rev to match. Git hooks re-installed from the main checkout; none points into `.worktrees/`.
- **2026-10-18T17:41:12-07:00** — PR opened: https://github.com/gitronald/gdrives/pull/70
