# Hooks named in the config: a design note

- Written: 2026-09-27T18:12:45-07:00
- Live API: none. Read from the code on the step's branch, with step 9 (the
  `transform` hook) merged in.
- Scope: step 11 (a `hooks` field naming `validate`, `check`, `warn`, and
  `transform` as `module:function`) starts as a proposal. This note answers
  the spec's open questions from the code, gives the options for each with
  what each costs, and ends with a verdict: whether to implement, and how.

## What the code has today

**C1. The hooks are per run, the config is per tab.** `run_target` takes one
`validate`, `check`, `warn`, and `transform` and passes the same callables to
every tab it runs. `validate` and `transform` are given the rows alone, with
no tab title, so one run-wide callable cannot dispatch to a tab's own hook. A
per-tab hook therefore has to reach the tab some other way than through
`run_target`'s existing arguments.

**C2. The hooks enter at three per-tab functions.** `plan_tab` (and through it
`sync_tab`), `pull_tab`, and `push_tab` take the hooks. `plan_tab` stores them
in the `TabPlan`, and the merge after a restructure (`_restructure`) re-plans
through the private `_plan` with the plan's stored hooks, so a hook composed at
`plan_tab`'s entry runs once per stage and is not composed twice. `push_rows`
takes no `TabConfig` and is outside this step.

**C3. The commands make requests before `run_target`.** `_run_config` loads
the config and checks the tabs, then announces credentials, resolves the
spreadsheet (`_resolve_and_report`, a Drive request for a Drive path), and
builds the service (which may wait on a consent). A problem found only inside
`run_target` is found after all of that.

**C4. `load_config` and `parse_config` are pure today.** They read one file and
build dataclasses; nothing is imported and nothing runs. `parse_config` is
what every test and the guide's examples use, with a mapping and a path.
The package's one `importlib` call (`values.decode_errors`) runs at import.

**C5. A hook's exception.** `run_target` catches `TAB_ERRORS` (`ValueError`,
`HttpError`, `OSError`) per tab, records the message as the tab's error, and
goes on. Anything else a hook raises (a `KeyError`, a `TypeError`) escapes
`run_target`, and `_cli_errors` does not catch it, so the command ends in a
traceback. A `validate`, `check`, or `warn` that returns a string is iterated
character by character into messages; one that returns None raises
`TypeError`. `_transformed` already checks what a transform returns, but a
transform returning None raises `TypeError` there too.

**C6. `run_target` refuses a code `transform` on a push**, before any request.

## The questions

### Q1. When the import happens

- **A. At `load_config`** (the spec's proposal). Every command, previews
  included, imports the named modules, and so does every library caller that
  only reads a config: the guide's examples, a tool that lists targets, a
  test. Import-time code of those modules runs with the user's credentials in
  reach whenever a config is merely read. A broken import stops a preview
  that did not need the hook yet. `parse_config` would lose its purity.
- **B. Lazily, as a step of its own.** `load_config` and `parse_config` check
  only the *form* `module:function` and import nothing. A separate
  `resolve_hooks(target, tabs)` imports the modules and looks the names up,
  listing every problem at once as a `ConfigError`. The commands call it right
  after `_config_tabs` and before the credential announcement and the first
  request (C3), so "checked before any request" holds for the commands.
  `run_target` calls it too, after its own argument checks and before the tab
  listing, so a library caller is refused before its first request as well.
  The per-tab functions resolve their tab's names when they start (C2), which
  after a pre-flight is a lookup in `sys.modules`. Cost: the module problems
  are raised by the resolving step, not "with the rest by `load_config`"; the
  form problems still are. A config read for any other purpose runs nothing.
- **C. Only inside the tab run.** A bad name is a tab error found after the
  listing request. Fails "before any request".

B is the only option that keeps reading a config free of code and still
finds every problem before the first request.

### Q2. Where a module is looked for

- **A. `sys.path` alone.** The module must be importable where `gdrives` runs:
  installed in the environment, as a downstream project's own package is
  under `uv run gdrives ...`, or on `PYTHONPATH`. The `gdrives` console
  script's `sys.path[0]` is its `bin` directory, not the working directory,
  so a file beside the config is not found unless the user puts it on the
  path. Nothing is shadowed and nothing global is changed.
- **B. The config's directory added to `sys.path`.** A file beside the config
  works without installing it. Costs: a `json.py` or `csv.py` beside the
  config shadows the standard library for the rest of the process (for a
  library caller, its whole process); a config dropped into a directory runs a
  file beside it; the path change must be undone, and anything imported under
  it stays in `sys.modules` after.
- **C. A path form, `path/to/file.py:function`**, loaded with
  `importlib.util.spec_from_file_location` under a private module name
  (`_gdrives_hooks.<n>`) that cannot shadow anything, resolved against the
  config's directory. Works for an uninstalled file without touching
  `sys.path`. Costs: a second syntax to check and document, and a file the
  config points at is run however it got there; relative imports inside it do
  not work.

A is the smallest and the safest; C can be added later without changing A.
B is refused.

### Q3. An exception inside a hook, and a wrong return

- **A. Leave it (C5).** A `ValueError` is the tab's error, anything else a
  traceback that stops the run and every later tab.
- **B. Wrap a config-named hook.** The resolved function is wrapped so any
  `Exception` it raises becomes a `ValueError` naming the target's tab, the
  hook, and the name (`tab 'Members': hook validate 'checks:ids' raised
  KeyError: 'id'`), which `run_target` records as the tab's error, exit 1, and
  the run goes on. The wrapper also refuses a `validate`, `check`, or `warn`
  that returns anything but a list or tuple of strings, and turns what a
  transform returns into a list inside the wrapper, so a None is caught
  there. Hooks given in code are not wrapped, so the library is unchanged.
  Cost: the traceback is lost; the message keeps the exception's type and
  text, and a caller debugging a hook can run it from code.

B, since for a config hook the command is the only caller and a traceback is
the alternative.

### Q4. Whether `parse_config` resolves names

No, under Q1 B: neither `parse_config` nor `load_config` resolves. Both check
the form, and `parse_config`'s lack of a directory stops mattering because
Q2 A needs none. (Under Q2 C the path would resolve against `path.parent`,
which `parse_config` already has.)

### Q5. What `TabConfig` holds

- **A. Names only**: `hooks: Mapping[str, str]`, hook to `module:function`,
  empty by default. It compares and prints like the other mapping fields
  (`schema`, `widths`), and the dataclass is already unhashable. A target's
  `hooks` is merged into each tab's at parse time, hook by hook, the tab's
  own name winning, so `Target` needs no new field. A target's `transform`
  is not given to its push tabs. `__post_init__` checks the form and refuses
  a `transform` on a push tab, as it checks `link_urls`.
- **B. Functions.** Forces resolution at parse time (Q1 A), prints as
  `<function ...>`, and makes two loads of one config compare equal only by
  identity of the functions.
- **C. Both.** The costs of B, and two fields that can disagree.

A.

### Q6. How a config hook and a code hook combine

The spec says both run, the config's first. At each of `plan_tab`,
`pull_tab`, and `push_tab`'s entry (C2), the tab's resolved hooks are joined
with the ones given: `validate`, `check`, and `warn` messages are
concatenated, config first; a config `transform` runs first and the code
`transform` is given its result. `run_target` needs only its pre-flight
(Q1 B). The alternatives, one `run_target` call per tab or per group of tabs
with equal hooks, would give up the single tab listing `run_target` makes and
split one report into several.

## Trust

A config that names hooks is code: running any of the three commands on it,
a preview included, imports the modules and calls the functions with the
user's credentials in reach. Under Q2 A a config can name only what is
already importable, so a hostile config chooses among installed callables and
feeds them rows it may control on the sheet. Getting harm from that is not
obvious, but it is not something to promise either. The guide section opens
with that warning. A flag (`--hooks`) that a command must be given before it
runs a config's hooks was considered and not recommended: a config's hooks
are normally the owner's own checks, and a flag everyone passes protects no
one.

## What it costs to build

- New module `gdrives/sheets/hooks.py`: the form check, `resolve_hooks`, the
  wrapper, and the join. Not a shared file.
- `config.py`: `"hooks"` in the tab and target fields, a `hooks` field at the
  end of `TabConfig` and its `__post_init__` check, one checker method, and
  the target default merged into each tab.
- `sync.py`: one line at the entry of each of `plan_tab`, `pull_tab`, and
  `push_tab`, and the pre-flight in `run_target`.
- `commands.py`: one call in `_run_config`.
- Tests in a new file; the guide section; README; CHANGELOG.

The edits to the shared files are local, and none reorders existing code.

## Recommendation and verdict

Q1 B (lazy, a step of its own before the first request), Q2 A (`sys.path`
alone, no path form in this step), Q3 B (wrap config hooks), Q4 no, Q5 A
(names only), Q6 join at the per-tab entry points.

**Verdict: SOUND.** The spec's design is doubtful as proposed, because it
imports at `load_config` and would add the config's directory to `sys.path`.
With both changed as above, reading a config runs nothing, a config shadows
nothing, every name is checked before the first request, a config with no
`hooks` imports nothing, and a code caller's hooks behave as today. The one
deviation from the spec: a module that does not import, or lacks the name, is
listed by `resolve_hooks` and not by `load_config`.
