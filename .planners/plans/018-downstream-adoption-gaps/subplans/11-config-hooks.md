# Name a run's hooks in the config file

Step 11 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second
list of gaps by the same downstream caller.

## Spec

### Goal

`gdrives sheets-sync`, `sheets-pull`, and `sheets-push` run the hooks a config names.
The commands take no hooks, so a caller with checks in code keeps a command of its
own that calls `run_target`. With the hooks named in the config, the caller's checks
run under the stock commands.

### This step starts as a proposal

It is one of the two lower-value steps, and its design has open questions. The first
deliverable is a design note in `implementation-notes/`, which answers the questions
below from the code. **If the design looks doubtful once the code is open, the step
stops at the note**, the options are written up in this plan's Log, and nothing is
merged but the note.

### Proposed design

- **Config:** a tab field `hooks`, an object with any of `validate`, `check`, and
  `warn`, and `transform` once step 9 has landed. Each value is a string
  `module:function`. A target may give `hooks` too, as the default for its tabs.
- **Opt-in.** A config with no `hooks` imports nothing and runs as today.
- **A config that names code runs code.** The guide says so plainly, at the top of
  the section: running a command on a config is running the functions it names, with
  the user's credentials in reach, so a config from somewhere else is read before it
  is run.
- **Checked before any request.** A name that is not of the form `module:function`,
  a module that does not import, a name the module lacks, and a value that is not
  callable are config problems, listed with the rest by `load_config`.
- **The library is unchanged for callers in code.** `run_target` takes the hooks it
  takes today. A hook given in code and one named in the config for the same tab
  both run, the config's first.

### Open questions for the note

- **When the import happens.** At `load_config`, every command imports the hooks,
  previews included, and a broken import stops a preview. At the run, a problem is
  not known "before any request" unless the import is a step of its own before the
  first tab. Which of the two the config checker can do without importing by default
  for library callers who only want to read a config.
- **Where a module is looked for.** On `sys.path` alone, a config's hooks must be
  installed. With the config's directory added, a file beside the config works, and
  a config can shadow an installed module. Whether to add it, and for how long.
- **An exception inside a hook.** Reported as the tab's problem, with the hook's
  name, or left to stop the run.
- **Whether `parse_config` resolves names** or only `load_config` does, given that
  `parse_config` takes a mapping and has no directory.
- **What `TabConfig` holds**: the names, the functions, or both, and what that does
  to a frozen dataclass that is compared and printed.

### Tests

- A config with hooks runs each of them, for each command, with `CliRunner`.
- Each config problem above is reported before the fake service records a request.
- A config with no `hooks` imports nothing: a test asserts that `importlib` is not
  called.
- A hook's messages reach the report as they do from code.
- The modules the tests import are written under `tmp_path`.

### Docs

- `docs/sheets-sync.md`: a section on hooks in the config, which opens with the
  warning.
- README config summary.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Arguments to a hook from the config.
- Hooks for `sheets-pull --all-tabs`, which takes no config.
