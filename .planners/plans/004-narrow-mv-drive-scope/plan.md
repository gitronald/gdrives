---
id: 4
slug: narrow-mv-drive-scope
status: draft
branch:
created: 2026-09-11T20:49:17-07:00
concluded:
pr:
---

# Narrow the mv command's OAuth scope to drive.metadata

## Plan

### Background

The `mv` command shipped in v0.9.0 requesting the full Drive scope:

```python
# gdrives/auth.py
DRIVE_WRITE_SCOPES = ["https://www.googleapis.com/auth/drive"]
```

The pre-release security review flagged this as a least-privilege mismatch. `mv`
only ever calls two Drive endpoints:

- `files.get` with `fields="id, name, mimeType, parents, driveId"` (via
  `gdrives.mv.get_metadata`, which also serves the destination check and the
  upward parent walk that refuses a move into the item's own subtree, plus
  `resolve.py`'s `files.list` during path resolution), and
- `files.update` mutating only `body.name`, `addParents`, and `removeParents`
  (`gdrives.mv.apply_move`).

It never reads or writes file **content**, manages permissions, or touches trash.
Google documents `https://www.googleapis.com/auth/drive.metadata` as sufficient
for `files.get` / `files.update`, so the grant cached in
`gdrives_token_drive.json` is strictly broader than anything the code exercises.
The concern is blast radius if that refresh token is ever exfiltrated: under the
full scope a stolen token can read, overwrite, or delete every file in the user's
Drive; under `drive.metadata` it can only rename and reparent.

### Scope

Determine empirically whether `drive.metadata` supports every call `mv` makes,
and narrow `DRIVE_WRITE_SCOPES` to it if so.

The uncertainty is real and cannot be settled by reading docs alone — the scope
tables list `drive.metadata` for these methods, but the reparent path
(`addParents` / `removeParents`) and the shared-drive path
(`supportsAllDrives=True`) need live confirmation. The deliverable is a verified
answer, not an assumed one.

### Approach

1. **Verify against the live API.** Authorize a token under
   `["https://www.googleapis.com/auth/drive.metadata"]` and exercise each path
   against a scratch folder, confirming both success and the resulting state:
   - pure rename (`body.name` only),
   - pure move (`addParents` + `removeParents`),
   - move + rename in one call,
   - the `files.list` path resolution `mv` performs before the update,
   - a folder move, which adds the upward parent walk (one `files.get` per
     ancestor),
   - the same in a shared drive with `supportsAllDrives=True`, if one is
     available to test against.

   Record any call that 403s under the narrower scope — that is the finding, and
   it decides the outcome.

   A service account asserts its own scopes, so it can request `drive.metadata`
   with no consent flow — the cheapest way to run this check. `mv` has no live
   tests today (only the Sheets and Docs integration suites exist), so the
   verification is a one-off script or a new live test, not a rerun of an
   existing one.

2. **If every path succeeds**, change `DRIVE_WRITE_SCOPES` to
   `["https://www.googleapis.com/auth/drive.metadata"]`. Check whether
   `_token_name` should map the new scope set to a stable filename in
   `_TOKEN_NAMES` (the current `gdrives_token_drive.json` name is derived, so a
   scope change silently renames the token file to
   `gdrives_token_drive-metadata.json` — decide whether to pin the old name for
   continuity or let the derived name change). The choice decides more than the
   file name: a token is looked for at the historical name first and then at
   the derived one (`_token_paths`), so a pinned name is also what puts the old
   full-scope token back in the lookup (step 4).

   `gdrives login --scope drive` grants `LOGIN_SCOPES["drive"]`, which is
   `DRIVE_WRITE_SCOPES`, so it follows the constant and grants `drive.metadata`
   with no further change. Decide whether the name `drive` still says what it
   grants, and reword its `--scope` help either way.

3. **If some path fails**, document which call needs the broader scope and why,
   and record the decision to keep `drive` — an answered question closed as
   `retired` is a valid outcome here.

4. **Migration.** What an existing user meets on the next `mv` depends on the
   filename step 2 settles on, because a cached grant now serves the scopes it
   implies: `_covers` tests a request against the grant and everything
   `_IMPLIES` lists for it, and `drive` implies `drive.metadata`
   ([plan 008](../008-oauth-token-and-consent-safety/plan.md)).

   - Under a **pinned** name the old full-`drive` token is found first, covers
     the request by implication, and is loaded with the scopes it records. No
     consent runs, and `mv` goes on using the full-scope token: the narrowing
     reaches new users only. A consent that does run later (the old token's
     refresh was refused) does not replace the file either, since it holds a
     grant the new one does not include: the new token goes to the derived
     name, with a warning, and the old file stays first in the lookup.
   - Under the **derived** name the old file is never opened, and the user
     consents once where a consent can run (client secrets, and a terminal or
     `gdrives login`). Where none can, the run falls through to a service
     account or ADC as it does today.

   Confirm whichever path is chosen actually fires rather than assuming it.

   Either way the old full-scope `gdrives_token_drive.json` stays on disk
   holding a live refresh token — the exact exposure this plan exists to
   remove — and under a pinned name it stays in use as well. Decide how it is
   retired: warn that it exists, or document the manual removal and
   revocation. Deleting it from code is at odds with plan 008, under which no
   consent replaces a file holding a grant the new one does not include, since
   the file may be a token its owner still needs; if this plan deletes it
   anyway, say why that rule does not apply here. Deleting the file does not
   revoke the grant, so the docs should say how to revoke it from the Google
   account as well.

5. **Update the docs** that name the scope: `README.md` (auth section and the
   `mv` section), `docs/setup-oauth.md`, `docs/setup-service-account.md` (the
   Contributor sharing note), `docs/setup-adc.md` (the ADC login scope), and the
   `mv` module docstring in `gdrives/mv.py`. Since plan 008 there is more that
   names it: the two token tables and the `gdrives login --scope drive` line in
   `docs/setup-oauth.md`, the `--scope` help of `login` in `gdrives/cli.py`,
   the comment above `DRIVE_WRITE_SCOPES` in `gdrives/auth.py`, the token list
   under "Security & privacy" in `README.md`, and the `mv` and token
   paragraphs of `.claude/CLAUDE.md`.

6. **Tests.** The unit tests mock the Drive service, so they assert the scope
   constant rather than the API's behavior. The `mv` scope test compares against
   `DRIVE_WRITE_SCOPES` itself, not the literal URL, so it passes unchanged; add
   tests for whatever step 2 and step 4 introduce (a pinned token name, handling
   of the old token file), and keep coverage at the `fail_under` floor.

   `tests/test_auth.py` does not pass unchanged. Plan 008's tests use
   `DRIVE_WRITE_SCOPES` as their full-`drive` grant: one that covers a
   `spreadsheets` and `documents` request, and a caller's own token that
   serves a Sheets request. `drive.metadata` implies neither, so those tests
   fail once the constant narrows. Give them the full `drive` scope by its URL,
   or by a name of its own, before the constant changes.

### Out of scope

- The read-only default (`drive.readonly`), the Sheets (`spreadsheets`), and the
  Docs (`documents`) scopes. Each is already minimal for its commands.
- Any change to `mv`'s behavior, arguments, or guards.

### Notes

Both `drive` and `drive.metadata` are *restricted* scopes under Google's
verification policy, so narrowing does not change the app-verification posture —
only the blast radius of a leaked token.

Plan [009](../009-drive-upload-and-sheet-create/plan.md) proposes commands that
request the full `drive` scope and cache it in `gdrives_token_drive.json`. If it
lands, that file is a token in use and not a leftover, which bears on how step 4
retires it. Plan 009 expects the broader token to serve `mv` too; that holds
only under a pinned name, since under the derived name `mv` never opens that
file.

## Log

### 2026-09-27 — Reviewed the draft against the current code

Checked the plan's premises before picking it up. They hold: `DRIVE_WRITE_SCOPES`
is still the full `drive` scope, `mv` is still its only consumer, and nothing
added since needs a Drive write scope. Changes to `mv` since the draft (the
every-parent descendant walk) add `files.get` calls only.

Corrections made to the spec:

- Step 4 assumed an old full-`drive` token could be reused for a narrower
  request. `_token_covers` compares scope strings literally, so it cannot.
- Step 4 now covers the orphaned full-scope token file, which the draft missed.
- Step 6 no longer asks for an assert update that is not needed.
- Step 1 notes the service-account shortcut and the absence of live `mv` tests,
  and adds the folder-move path.

### 2026-09-27 — Revised for plan 008's token handling

Plan [008](../008-oauth-token-and-consent-safety/plan.md) shipped in v0.12.0,
after the review above, and changed what this plan's migration rests on. Checked
against `gdrives/auth.py` at v0.13.0. The premises of the plan itself still
hold: `DRIVE_WRITE_SCOPES` is the full `drive` scope and `mv` is its only
consumer outside `LOGIN_SCOPES`.

What changed:

- A grant serves the scopes it implies (`_covers`, `_IMPLIES`), and `drive`
  implies `drive.metadata`. The entry above says an old full-`drive` token
  cannot be reused for a narrower request; that was true of the literal
  comparison, which is gone, and `_token_covers` with it.
- A token is looked for in two places, the historical name and then the derived
  one (`_token_paths`).
- A consent never replaces a file holding a grant the new one does not include
  (`_consent_path`, `_write_consent_token`).
- `gdrives login --scope drive` exists, and grants `DRIVE_WRITE_SCOPES`.

Corrections made to the spec:

- Step 2 says that the filename also decides whether the old token is in the
  lookup, and asks what `--scope drive` should be called.
- Step 4 is rewritten by filename: a pinned name keeps the full-scope token in
  use with no consent, and the derived name leaves it unopened. Retiring the old
  file is weighed against plan 008's rule on replacing tokens.
- Step 5 lists the docs and help text plan 008 added.
- Step 6 names the `tests/test_auth.py` tests that use `DRIVE_WRITE_SCOPES` as
  a full-`drive` grant and fail once it narrows.
- The Notes record the order with plan 009.

Nothing was implemented, and step 1's live check has not been run.

### 2026-09-29 — A narrow scope for uploads: two services

Noted while plan 020 was in progress. Nothing was implemented, and nothing below
was checked against the API.

`upload` and `sheets-create` request `DRIVE_WRITE_SCOPES` as `mv` does, so the
constant has three consumers where this plan's Background names one. Narrowing it
for `mv` to `drive.metadata` would take the content writes of the other two with
it, so they need a constant of their own first.

Plan 019's Log holds that under `drive.file` a listing is partial, so a replace
can miss a file of the name and `--no-replace` can report a taken name as free. A
caller proposed a way round: two services.

- The listing (`find_named`, and the path resolution before it) runs on the
  read-only token, `drive.readonly`, which sees every file of the name.
- The write alone runs on a `drive.file` token.

What that gives:

- `--no-replace` keeps its promise: the refusal rests on the whole listing.
- A create (`files.create`, and `sheets-create --from`) needs no more than
  `drive.file`, in a folder the token can write to.

What it does not solve, each to be read from the API before a design rests on it:

- **A replace in place of a file the app did not create.** The listing sees the
  file, and `files.update` under `drive.file` is expected to be refused for it.
  The narrow mode would then create, or refuse, where the full scope replaces.
- **A folder the app did not create.** Whether `files.create` with such a folder
  as its parent is allowed under `drive.file` decides whether the mode is of use
  in a shared folder at all.
- **The read-back.** `verify` reads the file's size and checksum; under
  `drive.file` that is a file the app has just created, which it may read.
- **Two tokens are held** where one was, and the read-only one is the broader of
  the two for reading. The gain is that the token that can write can write only
  what the app made.

The token-file questions of step 2 and step 4 apply to a `drive.file` token as
they do to a `drive.metadata` one.
