---
id: 5
slug: template-upgrade
status: draft
branch:
created: 2026-09-25T23:35:55-07:00
concluded:
pr:
---

# Upgrade to the proj-template 0.10.0 standard

## Plan

Bring gdrives up to the current proj-template standard with the template's
`install-template` skill (upgrade mode), then stamp the applied release in
`pyproject.toml`. The release is 0.10.0: `template/` is byte-identical between the
template's `v0.10.0` tag and its current `dev`, so no unreleased template content
is involved.

### Classification

**package** — hatchling `[build-system]`, a `project.scripts` entry
(`gdrives = "gdrives.cli:app"`), and an importable `gdrives/` package that
`publish.yml` builds from tags. Every package row of the sync matrix applies.

### Sync decisions

| Template path | Decision |
|---|---|
| `pyproject.toml` `[tool.ruff*]` | Already identical; no change |
| `pyproject.toml` `[tool.pyrefly*]` | Template content already present; keep the repo's `search-path = ["tests"]` |
| dev group (`pre-commit`, `pyrefly`, `pytest`, `pytest-cov`, `ruff`) | All present; keep the repo's newer floors (`pyrefly>=1.2.0`, `pytest>=9.1.1`, `ruff>=0.16.4`) |
| `[tool.pytest.ini_options]`, `[tool.coverage.*]` | Already merged (bare `--cov`, `run.source = ["gdrives"]`); keep the repo's extras (`pythonpath`, `markers`, `show_missing`) and its `fail_under = 100`, which is stricter than the template's 50 |
| `[build-system]`, sdist `only-include`, `[project.urls]`, `[project.scripts]` | Already identical; bring in the template's second sdist comment paragraph (hatchling force-includes); keep `license` and `[tool.hatch.build.targets.wheel]` |
| `[tool.proj-template]` `version` | Add after `[project.scripts]` with the template's comment, stamped `0.10.0`; written last |
| `.pre-commit-config.yaml` | Older template revision (ruff-pre-commit `v0.16.4`, `planners-validate` sharing the pyrefly `local` block); replace with the template (`v0.16.6`, separate block) |
| `.python-version` | Already `3.14`; no change |
| `.gitignore` | Already carries every template entry; keep `.gdrives/` |
| `.claude/settings.json`, `.claude/settings.local.json`, `.claude/hooks/lint-typecheck.sh` | Absent; copy. `.claude/` is gitignored, so they land in the main checkout, outside the branch |
| `.claude/CLAUDE.md` | Already at the canonical path, `## Development` bullets current. "Before finishing a task" lists two checks where the template lists three; merge the template wording (user decision, see Log) |
| `.github/workflows/test.yml` | Older template revision (bare `actions/checkout@v7`, `setup-uv` v8.3.2); replace with the template's SHA pins (checkout v7.0.1, setup-uv v10.0.1), `env` after `strategy`, and the `--python` comment |
| `.github/workflows/publish.yml` | Older template revision (bare `checkout@v7`, `upload-artifact@v7`, `download-artifact@v8`, `gh-action-pypi-publish@release/v1`); replace with the template's SHA pins |
| `.github/dependabot.yml` | Add the template header comment and `target-branch: dev` on both ecosystems (`origin/dev` exists). Repo toggles already match: alerts on, automated security fixes off |
| `.planners/` scaffold | Already present, with `merge=union` on the index |

### Deliberately skipped

- `gdrives/`, `tests/`, `README.md`, `CHANGELOG.md` — never synced.
- `.github/renovate.json`, `.github/workflows/renovate.yml` — the repo already
  uses Dependabot, and one update automation is enough.
- `uv.lock` — no dependency changes; version bumps stay with Dependabot.

### Implementation order

1. Tooling config: the `pyproject.toml` sdist comment and `.pre-commit-config.yaml`.
2. CI: `test.yml`, `publish.yml`, and `dependabot.yml`.
3. `.claude/` payload in the main checkout: both settings files, the Stop hook, and
   the CLAUDE.md wording.
4. Verify in the worktree: `uv sync --all-groups`, `ruff check`, `ruff format
   --check`, `pyrefly check`, `pre-commit run --all-files`, and `pytest` (coverage
   floor 100). Then repair the shared pre-commit hook from the main checkout.
5. Stamp `[tool.proj-template] version = "0.10.0"` last.
6. Push, open a PR into `dev`, merge once CI is green, close this plan, and remove
   the worktree.

### Follow-ons

- `dependabot.yml` is read from the default branch, so `target-branch: dev` stays
  inert until the next release carries it to `main`. Until then Dependabot keeps
  opening PRs against `main`; the open github-actions PR (setup-uv 8.3.2 to
  10.1.0) edits the same lines this upgrade re-pins.
