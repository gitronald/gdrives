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
  `gdrives.mv.get_metadata`, plus `resolve.py`'s `files.list` during path
  resolution), and
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
   - the same in a shared drive with `supportsAllDrives=True`, if one is
     available to test against.

   Record any call that 403s under the narrower scope — that is the finding, and
   it decides the outcome.

2. **If every path succeeds**, change `DRIVE_WRITE_SCOPES` to
   `["https://www.googleapis.com/auth/drive.metadata"]`. Check whether
   `_token_name` should map the new scope set to a stable filename in
   `_TOKEN_NAMES` (the current `gdrives_token_drive.json` name is derived, so a
   scope change silently renames the token file — decide whether to pin the old
   name for continuity or let the derived name change).

3. **If some path fails**, document which call needs the broader scope and why,
   and record the decision to keep `drive` — an answered question closed as
   `retired` is a valid outcome here.

4. **Migration.** Existing `gdrives_token_drive.json` grants are unaffected in
   either direction: `_token_covers` discards a cached token whose granted scopes
   do not cover the request, so a user re-consents once on next `mv`. Confirm
   that path actually fires rather than assuming it (a *narrower* request is
   still covered by a broader existing grant, so an old full-`drive` token may be
   reused rather than re-consented — decide whether that is acceptable or whether
   the old token should be invalidated).

5. **Update the docs** that name the scope: `README.md` (auth section and the
   `mv` section), `docs/setup-oauth.md`, `docs/setup-service-account.md` (the
   Contributor sharing note), `docs/setup-adc.md` (the ADC login scope), and the
   `mv` module docstring in `gdrives/mv.py`.

6. **Tests.** The unit tests mock the Drive service, so they assert the scope
   constant rather than the API's behavior — update whichever assert names
   `auth/drive`, and keep coverage at the 100% floor.

### Out of scope

- The read-only default (`drive.readonly`), the Sheets (`spreadsheets`), and the
  Docs (`documents`) scopes. Each is already minimal for its commands.
- Any change to `mv`'s behavior, arguments, or guards.

### Notes

Both `drive` and `drive.metadata` are *restricted* scopes under Google's
verification policy, so narrowing does not change the app-verification posture —
only the blast radius of a leaked token.
