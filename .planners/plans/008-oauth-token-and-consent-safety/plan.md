---
id: 8
slug: oauth-token-and-consent-safety
status: active
branch: feature/oauth-token-and-consent-safety
created: 2026-09-27T11:03:57-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/45
---

# Protect a caller's OAuth token and make a pending consent visible

## Plan

### Goal

Stop an upgrade from replacing a token a caller wrote itself, let a run start a consent
when no terminal is attached, and say so before any run that is about to wait on one.

The three findings are M1 to M3 of a review that followed a downstream caller from
0.5.8 to 0.11.0
([note 003 of plan 007](../007-sheets-sync-adoption-gaps/implementation-notes/003-downstream-migration-review.md)).
Plan 007's note 004 checked each against the code at `v0.11.0`, and all three hold. They
are authentication, not sheets, so they are a plan of their own. M1 goes first: it
breaks a caller at upgrade time, before the caller changes any code.

### Scope

In scope:

1. A cached token whose grant is broader than the request is used as it is [M1].
2. A token file is never overwritten by a consent for a grant that does not include
   the one it holds [M1].
3. A way to start a consent with no terminal attached: a `force` argument in the
   library and a `gdrives login` command [M2].
4. The credential line printed whenever a consent or a refresh is coming, preview or
   not [M3].

Out of scope:

- Narrowing the scope `mv` requests, which is plan
  [004](../004-narrow-mv-drive-scope/plan.md). The table of item 1 has to agree with
  what 004 settles.
- New scopes. Plan [009](../009-drive-upload-and-sheet-create/plan.md) decides what
  upload needs.
- Service accounts and ADC, which hold no cached token to protect.

### Design

#### 1. A broader grant covers a narrower request [M1]

0.5.8 had no write scope, so a caller that needed one wrote its own token. The natural
name was `gdrives_token_rw.json` and the natural scope was the full `drive` scope. 0.6.0
claimed that filename for the `spreadsheets` scope (`auth.py:57-60`). `_token_covers`
compares scope sets literally (`auth.py:125`), so a token granted `drive` does not cover
a request for `spreadsheets`, and `authenticate_oauth` starts a consent and writes the
new token over the old one (`auth.py:217-236`). The caller's own code then loads a token
with no Drive access and gets a 403.

This happens only where a consent can run, which needs the client secrets file and a
terminal. Without them the run falls through to a service account and the token is left
alone.

- `_IMPLIES` maps a scope to the scopes a grant of it serves: `drive` serves
  `drive.readonly`, `drive.file`, `drive.metadata`, `spreadsheets`, and `documents`,
  and each write scope serves its own `.readonly`. The Sheets and Docs APIs accept the
  `drive` scope for their own methods.
- `_token_covers` tests the request against the grant and everything it implies.
- A token that covers a request by implication is loaded with the scopes it records,
  not the ones requested. A refresh that asks for a scope outside the grant can be
  refused, which would bring the consent back by another road.

#### 2. No consent overwrites a grant it does not include [M1]

When the cached token does not cover the request, a consent runs and its token is
written to the request's file. If that file holds a grant the new one does not include,
the write destroys it.

- Before writing, the file's recorded grant is compared with the new one. When the new
  grant includes it, the file is replaced, as today.
- When it does not, the new token goes to the name derived from its scopes
  (`_token_name`'s fallback, for example `gdrives_token_spreadsheets.json`), the old
  file is left alone, and one line on stderr says which file was written and why.
- `_token_path` then looks in both places: the historical name, then the derived one.
- A file with no recorded scopes, or one that does not parse, is never overwritten.
- The setup docs name the token filenames as reserved, and the changelog says what
  changed.

#### 3. A consent without a terminal [M2]

`_can_consent` requires `stdin` to be a TTY (`auth.py:141-143`, `auth.py:195-197`).
Without one, OAuth is skipped and authentication falls through to a service account or
ADC, which usually cannot see a folder shared with a person. A run started by a tool
that captures `stdin`, with a person still watching the output, cannot start the flow.

- `authenticate_oauth(scopes, *, force=False)`, and the same argument on
  `authenticate` and the `build_*` helpers. With `force`, the flow runs whether or not
  `stdin` is a TTY, and a missing client secrets file is an error, not a fall-through.
- `gdrives login [--scope NAME]` runs it: `read` (the default), `sheets`, `docs`, or
  `drive`. It prints the URL, waits for the redirect, writes the token under the rules
  of item 2, and prints the credential line. It is also the way to grant again after a
  refresh has failed.
- The flow waits for a person, so it takes `--timeout SECONDS` and exits 1 when the
  time runs out, with the token files untouched.
- No command gains a `--login` flag. One command that does the consent is simpler
  than a flag on every write command, and a person runs it once.

#### 4. Announce a pending consent [M3]

The credential line is printed only with `--apply` (`commands.py:415`,
`commands.py:540`). A preview uses the read-only token, and when that token cannot be
refreshed the preview waits on a consent with nothing printed first.

- The line is printed whenever `describe_credentials` reports `consent` or `refresh`,
  preview or not, and with `--apply` always, as today.
- Plan 006's Log noted that only the three sync commands print the line. The helper
  moves out of `sheets/commands.py` to where every command that builds a service can
  call it, and each does, under the same rule.

### Compatibility

| Change | Who sees it |
|---|---|
| A token granted `drive` is used for a `spreadsheets` or `documents` request | A caller with such a token stops being asked to consent |
| A consent may write its token under a derived name | A caller that reads a token file by its historical name after such a consent |
| The credential line can appear on stderr during a preview | A caller that treats any stderr output as a failure |

### Testing

Coverage is gated at 100%. `tests/test_auth.py` already fakes token files, the flow,
and the TTY check.

- Item 1: each pair in `_IMPLIES`, a grant that does not cover, and the scopes a token
  is loaded with.
- Item 2: a file with a broader grant, a narrower one, an unrelated one, no recorded
  scopes, and unparseable content; the lookup order.
- Item 3: `force` with and without a TTY, with no client secrets file, and the
  timeout; the command's exit codes.
- Item 4: the line on a preview for each `CredentialInfo` state, and for a command
  outside `sheets-*`.
- No live test: a consent needs a person. The manual check is one `gdrives login` run
  by the owner, recorded in the Log.

### Open questions

- **Which scopes `drive` implies for the purpose of a cached token.** The table above
  is from Google's per-method scope lists. Each pair is checked against them before it
  is written down, and any pair that cannot be confirmed is left out.
- **Where a refused overwrite should leave the caller.** Writing under the derived
  name keeps both tokens. Refusing the consent outright is the stricter alternative,
  and it leaves the write command unable to run.
- **Order with plan 004.** If 004 narrows `mv` to `drive.metadata` first, the table
  gains that pair and `gdrives login --scope drive` asks for what `mv` asks for.

## Log

### 2026-09-27 — runs before plan 007, which may overlap it

Decided by the owner on 2026-09-27T11:47:03-07:00, before either plan was activated:
this plan is implemented first, and plan
[007](../007-sheets-sync-adoption-gaps/plan.md) may be in progress at the same time, in
its own worktree.

The two share no design. They share files:

| File | This plan | Plan 007 |
|---|---|---|
| `gdrives/sheets/commands.py` | Item 4: the credential line moves out, and prints on a preview | Step g: retry notices, `--all-tabs` options, and `sheets-widths` |
| `gdrives/cli.py` | `gdrives login`, and the credential line on every command | Step g: one command and two options |
| `CHANGELOG.md`, `README.md`, the project `CLAUDE.md` | Lines added | Lines added by every step |
| `docs/sheets-sync.md` | Its section on credentials, if item 4 changes what it says | Steps a to h |

What follows from the order:

- This plan is cut from `dev` and merges into `dev` without waiting for 007. It is the
  smaller of the two, and M1 is a hazard at upgrade time.
- 007's overlap is in its step g, the seventh of eight. Once this plan has merged,
  007's umbrella branch takes `dev` in before step g starts, so that step g builds on
  the moved credential helper.
- This plan has no live tests, so 007 has the Sheets quota to itself.
- If this plan ships as a release of its own, its changelog lines are promoted before
  007 merges into `dev`. 007 lands through one umbrella PR at its end, so its lines do
  not reach `[Unreleased]` on `dev` before then.

### 2026-09-27 — items 1 to 4 implemented

Written at 2026-09-27T12:01:02-07:00, on `feature/oauth-token-and-consent-safety`.
Commits: `c706b43` (the library: items 1, 2, and the `force`, `timeout`, and
announcement helpers), `cbe3807` (`gdrives login`, and the announcement in every
command), then the docs.

The open questions:

- **Which scopes `drive` implies.** Checked against the per-method scope lists in the
  discovery documents of Sheets v4 (revision 20260921), Docs v1 (20260921), and Drive
  v3 (20260923), 84 methods in all: a pair is kept when every method that accepts the
  narrower scope also accepts the broader one. Every pair of the design holds. Three
  more follow from it and are in the table: `drive` serves `drive.metadata.readonly`,
  `spreadsheets.readonly`, and `documents.readonly`. One pair the lists allow is left
  out on purpose: `drive.file` is accepted by the Sheets and Docs methods, but reaches
  only the files the app created or was handed, so it serves no other scope.
- **Where a refused overwrite leaves the caller.** Both tokens are kept: the new one
  goes to the derived name. Where the derived name is the historical one
  (`documents`, `drive`), or is taken by another grant too, the token serves the run
  and is not saved, with a warning naming the file in the way. The consent then comes
  back on the next run until that file is moved.
- **Order with plan 004.** 004 is still a draft. The table already holds the
  `drive.metadata` pair, and `gdrives login --scope drive` asks for
  `DRIVE_WRITE_SCOPES`, so it follows whatever `mv` asks for once 004 changes that.

Where the design left room:

- `_token_path` still returns the one historical name. The lookup in both places is
  `_token_paths`, and a token whose refresh is refused is passed over for the next
  place before any consent starts.
- `force` does not consent again when a cached token serves the request. It lifts the
  terminal requirement and turns OAuth that is not configured into a `ConsentError`.
- `--timeout` defaults to 300 seconds in the command and to no limit in the library. A
  timeout is caught as an `AttributeError`, because `WSGITimeoutError` exists only in
  releases of `google-auth-oauthlib` after 1.2.1 and the dependency has no floor.
- Item 4 is not a call in each command. `_cli_errors`, which every command already
  runs in, enters `announcing_credentials()`, and the one place credentials are made
  (`_credentials`) prints the line when a consent or a refresh is coming, once per
  scope set. A path given to a command is resolved with a Drive service of its own,
  which a call per command would have missed, and a new command cannot forget it. A
  library caller's stderr is unchanged outside such a block.
- The notice of a token written under the derived name is a `logger.warning`, like
  the module's other token notices. It reaches stderr through logging's default
  handler, which a real run confirmed.

Found while running it: with no terminal attached stdout is block-buffered, so the
consent URL stayed in the buffer until the command exited, which is the one case
`force` exists for. The prompt is now flushed as it is written.

Checks: `ruff`, `pyrefly`, and 1608 tests at 100% coverage. Runs of the real command
against a scratch config directory with placeholder client secrets and no terminal:
`gdrives login --timeout 2` printed the credential line and the URL, then exited 1 with
no token file written; `gdrives login --scope sheets` with a `drive` token under
`gdrives_token_rw.json` exited 0 and left the file byte for byte as it was; and a
missing client secrets file exited 1.

Still open:

- The manual check of the Testing section, one real `gdrives login` by the owner.
- The project `CLAUDE.md` is not tracked, so its update exists only in the worktree's
  copy and has to be carried to the main checkout when the branch merges.
