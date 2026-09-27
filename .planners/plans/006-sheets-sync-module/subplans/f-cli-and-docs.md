---
status: active
branch: feature/sheets-sync-f-cli-and-docs
---

# 006f — Wire the CLI commands and write the docs

Part of [006](../plan.md). Step 6 of the umbrella's implementation order. The
commands are thin wrappers over the functions in
[`e-config-and-orchestration.md`](e-config-and-orchestration.md).

## CLI

```bash
gdrives sheets-sync roster                       # Preview every tab of the target
gdrives sheets-sync roster --tab Members         # Preview one tab
gdrives sheets-sync roster --apply               # Write sheet, local file, and base
gdrives sheets-sync roster --apply --adopt       # First sync: rewrite the tab from local
gdrives sheets-sync roster --apply --add-missing # Add local columns the sheet lacks
gdrives sheets-sync roster --apply --drop-extra  # Delete sheet columns outside the projection
gdrives sheets-sync roster --apply --prefer local  # Resolve conflicts toward local
gdrives sheets-pull roster --apply               # Replace local files for the pull tabs
gdrives sheets-push roster --apply               # Replace the push tabs from local files
gdrives sheets-pull <sheet> --all-tabs -o out/   # One-off dump, no config
```

Every command **previews by default** and writes only with `--apply`. The
report lists pushes, folds, new rows, overrides, conflicts, and row flags per
tab, with each cell's before and after values passed through
`gdrives.local.printable`.

Exit codes let automation tell the outcomes apart:

| Code | Meaning |
|---|---|
| 0 | In sync, or every change applied |
| 1 | Error: config, schema, API, or a failed guard |
| 2 | Needs a person: conflicts or row flags remain |

`--apply` with conflicts present still applies the changes that do not
conflict, then exits 2.

## Docs

- `README.md`: a "Sync a Sheet with a local file" section.
- `docs/sheets-sync.md`: the guide, covering the three modes, the merge rules,
  ownership, the base snapshot, and the first sync. It also records one usage
  rule: a column the sheet owns must not be given a default by whatever
  generates the local file, because a default cannot tell "never filled in"
  from "cleared on purpose", and would be pushed back over a deliberate blank.
- `.claude/CLAUDE.md`: package structure and the Commands block.
- `CHANGELOG.md`: entries under `[Unreleased]`.

## Log

### 2026-09-27 — implementation

`run_sync`, `run_pull`, and `run_push` in `commands.py`, and the `sheets-sync`,
`sheets-pull`, and `sheets-push` commands in `cli.py`. Nothing in `sync.py` changed.

Decisions on points the sections above leave open:

- **Options.** A positional target, `--config`, `--tab` (repeatable), and `--apply` on
  all three. `sheets-sync` adds `--adopt`, `--add-missing`, `--drop-extra`, and
  `--prefer local|sheet`. `sheets-pull` adds `--all-tabs` with `-o`, `--skip`
  (repeatable), and `--format csv|tsv|json`.
- **`--all-tabs` requires `-o`** and refuses `--tab` and `--config`. `-o`, `--skip`, and
  `--format` are refused without `--all-tabs`.
- **Usage errors the commands catch themselves exit 1,** like the existing check that
  `--csv` and `--tsv` are mutually exclusive. Exit 2 is left for conflicts and row flags,
  and for the parse errors Typer raises itself (a bad `--prefer` value, say). The guide
  notes the overlap.
- **Tabs are checked before any request,** in `commands.py`: unknown tabs, tabs of
  another mode, and a target with no tabs of the mode are listed together.
- **Scopes.** A preview requests the read-only scope. `sheets-sync` and `sheets-push`
  request the write scope with `--apply`. `sheets-pull --apply` writes only local files
  and stays read-only.
- **The credential line** (`Credential: ...`, from `describe_credentials`) goes to stderr
  before the first request, on these three commands and only with `--apply`. The existing
  write commands are unchanged; adding the line to them is a possible follow-up.

Tests: `tests/test_sheets_commands.py` drives the Typer app with `CliRunner` against
`FakeSheetGrid`, covering each exit code, stdout and stderr, the scopes requested, and
every refusal, each checked to make no request. Two live tests were added: a sync round
trip (adopt, a local edit and a sheet edit on different cells, then a no-op run), and a
push that shrinks a 4 by 4 tab to 2 by 2. The second confirms against the real API what
step e left unverified: a blank string in the padded write clears the old cell.

Docs: `docs/sheets-sync.md` (the guide), a "Sync a Sheet with a local file" section and an
updated project tree in `README.md`, and `CHANGELOG.md` entries. `.claude/CLAUDE.md` is
not tracked in this repo, so its update is not part of any commit and has to be made in
each checkout by hand.

Review: three reviewers (conformance, correctness, and test quality) reported no
findings. Every command line in the CLI block above, the README, and the guide was parsed
through the real CLI with the entry points stubbed out. The orchestrating session scanned
the whole stack's diff for personal and private identifiers and found none.

Checks rerun after the workflow: ruff and pyrefly clean, 1543 tests pass, and coverage is
100%. The full run took 131 seconds, against about 40 before, because the live suite now
waits out the write quota instead of failing on it.

Commits: `29ce1b2`, `c9eeed2`, `6373075`, `8d9fb1e`, `4eae7a6`.
