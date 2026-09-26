---
id: 5
slug: template-upgrade
status: done
branch: feature/template-upgrade
created: 2026-09-25T23:35:55-07:00
concluded: 2026-09-25T23:40:40-07:00
pr: https://github.com/gitronald/gdrives/pull/32
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

## Log

### 2026-09-25 — upgrade applied on `feature/template-upgrade`

- Divergence triage: every managed file except `.claude/CLAUDE.md` was either
  identical or an older template revision with nothing repo-specific to lose, so
  those rows applied without a question. The one batched question covered
  CLAUDE.md, and the user chose **merge**: the "Before finishing a task" section
  now lists the template's three checks (adding `uv run ruff format --check .`),
  since the Stop hook installed here runs that check too. The rest of CLAUDE.md
  is untouched.
- Commits: `sync pre-commit and pyproject with template` (ruff-pre-commit
  `v0.16.6`, `planners-validate` in its own `local` block, the second sdist
  comment paragraph); `sync workflows and dependabot with template` (SHA pins,
  the `env` placement and `--python` comment in `test.yml`, `target-branch: dev`
  and the header comment in `dependabot.yml`); `stamp proj-template version
  0.10.0`, written after every other row was applied and verified.
- `.claude/` is gitignored, so the absent `settings.json`, `settings.local.json`,
  and `hooks/lint-typecheck.sh` were copied into the main checkout, and the
  CLAUDE.md edit was made there too. None of it rides in the branch.
- The repo's Dependabot toggles already matched the standard (alerts on,
  automated security fixes off), so nothing changed there.
- Verification in the worktree, all green: `ruff check`, `ruff format --check`
  (44 files), `pyrefly check` (0 errors), `pre-commit run --all-files`
  (ruff v0.16.6, pyrefly, planners-validate), and `pytest` (657 passed, coverage
  100% against the kept floor of 100).
- `pre-commit install` ran from the main checkout only, never from the worktree,
  so the shared `.git/hooks/pre-commit` kept its `INSTALL_PYTHON` on the main
  checkout's `.venv` (checked; nothing points into `.worktrees/`).
- Finding, not addressed here: `gdrives/auth.py` calls `load_dotenv()` at import,
  and python-dotenv searches parent directories for `.env`. From a worktree under
  `.worktrees/` it finds the main checkout's gitignored `.env`, so `uv run pytest`
  in a worktree runs the live integration tests against the test sheet and doc
  even though the worktree has no `.env` of its own. The verification run passed
  with them included. Later runs of only `-m integration` errored in the `tab`
  fixture's live `addSheet` setup call, before any tab was created; the error
  message was not captured.

### 2026-09-25 — PR #32 and review follow-up

- Opened PR #32 into `dev`. CI passed on all four matrix cells, and the job logs
  show distinct interpreters (e.g. CPython 3.11.16 and 3.12.14), so the moved
  `env` block still keeps the matrix honest.
- Review (level low: one correctness finder plus a gap sweep) found no confirmed
  defects. Checked clean: every action SHA pin resolves to the tag in its
  comment, and the SHA-pinned `gh-action-pypi-publish` resolves to an existing
  `ghcr.io` image tagged with that SHA. That matters here because the repo's
  `PUBLISH_ENABLED` variable is `true`, so the next release tag runs
  `publish.yml`.
- One plausible finding, taken as a conscious no-op: the pre-commit ruff hook
  (`v0.16.6`) no longer matches the locked ruff (0.16.4) that CI and the Stop hook
  run, so a formatter fix between the two could make the commit hook and CI's
  `ruff format --check` disagree. Not reproduced (both pass every file). Bumping
  the lock here would be superseded by Dependabot's open python-group PR (ruff
  0.16.8), which only reverses the skew. The lasting fix is at the template level:
  run ruff through `uv run` as a local hook, the way pyrefly already runs.

## Retrospective

- Every managed file but one was an older template revision with nothing
  repo-specific to lose, so the divergence triage came down to a single
  question (the CLAUDE.md check list). A repo that has had one upgrade before
  mostly needs its pins and comments refreshed.
- The riskiest content was the SHA pins in `publish.yml`: no PR CI run exercises
  them, and publishing is enabled here. Resolving each tag, and checking that the
  pypa action's SHA-tagged image exists, is what made them safe to merge.
- Worktree trap worth carrying forward: `load_dotenv()` at import walks up to the
  main checkout's `.env`, so a worktree's test run is not isolated from the live
  integration targets. Worth making explicit in the tests (e.g. a conftest guard)
  or in the upgrade skill's verification notes.
- Running `pre-commit run --all-files` without `pre-commit install` in the
  worktree sidestepped the shared-hook `INSTALL_PYTHON` trap entirely. The skill
  runs install inside the worktree and then repairs the hook, but skipping the
  in-worktree install avoids needing the repair at all.
