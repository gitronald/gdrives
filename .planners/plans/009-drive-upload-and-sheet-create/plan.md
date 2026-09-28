---
id: 9
slug: drive-upload-and-sheet-create
status: active
branch: feature/drive-upload-and-sheet-create
created: 2026-09-27T11:03:58-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/60
---

# Add file upload and spreadsheet creation to Drive writes

## Plan

### Goal

Let a caller put a local file in Drive, replacing an existing file's content in place,
and create a native spreadsheet in a folder, so that a project that does either can
drop its own Drive write code.

The package reads, exports, downloads, renames, and moves. It cannot put a local file
in Drive or create a spreadsheet. Both are M10 and M11 of a review that followed a
downstream caller from 0.5.8 to 0.11.0
([note 003 of plan 007](../007-sheets-sync-adoption-gaps/implementation-notes/003-downstream-migration-review.md)).
Plan 007's note 004 confirmed both absences. They add Drive writes, not sheets sync, so
they are a plan of their own.

### Scope

In scope:

1. `gdrives upload LOCAL DEST`, and the helpers behind it in `gdrives/upload.py` [M10].
2. `gdrives sheets-create`, which creates a native spreadsheet in a folder and prints
   its URL [M11].

Out of scope:

- Converting an uploaded file to a Google format (`.xlsx` to a native Sheet). Plan 006
  left it out, and it stays out.
- Uploading a folder. One file per run.
- Creating a spreadsheet as a step of `sheets-push`. A config names its spreadsheet by
  URL, ID, or path, so a run that created one would have to rewrite the config. The
  command prints what to put there.
- Deleting or trashing anything.

### Design

#### 1. Upload [M10]

The caller in view puts a rendered PDF in a Drive folder, and replaces the content of
the existing file so that its ID and every shared link stay the same.

- `DEST` decides the operation, as it does for `mv`. A path that resolves to an
  existing folder uploads into it under the local file's name. A path whose parent
  exists and whose last segment does not uploads under that name. `--dest-id` takes a
  folder ID and skips path resolution.
- One file in the folder with that name: its content is replaced in place
  (`files.update` with a media body), so the file ID and the links survive. Its name
  and parents are not touched.
- No such file: it is created (`files.create`) with the folder as its parent.
- Several files with that name: refused, with each file's ID listed, as `mv` refuses
  what it would have to guess at. `--file-id` names the one to replace.
- A Google-native file of that name (a Doc, Sheet, or Slides file) is refused: its
  content cannot be replaced by an upload.
- `--dry-run` resolves everything and prints the operation (`create` or `replace`,
  the target's name and ID, and the size), and makes no write. It stays on the
  read-only scope, as `mv --dry-run` does.
- The MIME type is guessed from the local file's extension, with `--mime-type` to
  set it. The upload is resumable, so a large file survives a dropped connection.
- Every call passes `supportsAllDrives=True`.
- After the write, the file's metadata is read back, and its size and MD5 checksum
  are compared with the local file's. A mismatch exits 1.
- The command prints the file's ID and URL.

#### 2. Create a spreadsheet [M11]

A push creates a missing tab but not a missing spreadsheet. The caller in view created
one in a given folder on its first run, then removed the default `Sheet1` once its own
tabs existed.

- `gdrives sheets-create --title TITLE [--folder PATH | --folder-id ID] [--tab TITLE ...]`
  creates a native spreadsheet and prints its URL and ID, as `docs-create` does for a
  Doc. With no folder it goes in My Drive's root.
- The file is created through the Drive API (`files.create` with the spreadsheet MIME
  type and the folder as parent), since the Sheets API creates in the root only.
- A new spreadsheet has one tab, `Sheet1`. With `--tab`, that tab is renamed to the
  first title given and the others are added after it. Nothing is deleted, so the rule
  that a tab is never deleted holds with no exception for a fresh file.
- Without `--tab` the default tab is left as it is.
- A file of the same name in the folder is not an error, since Drive permits
  duplicates, and the command says so when it finds one. `--dry-run` reports it
  without creating anything.
- `create_spreadsheet(drive, sheets, title, *, folder_id=None, tabs=())` is the
  library function, returning the new file's ID.

### Scopes

Both commands request the `drive` scope that `mv` uses, cached in
`gdrives_token_drive.json`. `spreadsheets` alone cannot place a file in a folder, and
`drive.file` covers only files the app created or opened, so it cannot replace a file
that something else made.

Plan [004](../004-narrow-mv-drive-scope/plan.md) proposes narrowing `mv` to
`drive.metadata`, which cannot write content. If 004 lands, `mv` and these commands
request different scopes and cache different tokens, and
[plan 008](../008-oauth-token-and-consent-safety/plan.md)'s table lets the broader
token serve `mv` too.

### Testing

Coverage is gated at 100%.

- Upload: create, replace, several matches, a Google-native match, `--file-id`,
  `--dry-run`, a checksum mismatch, a missing local file, and a destination whose
  parent does not exist. The Drive fake gains `files.create` and `files.update` with a
  media body.
- Create: with and without a folder, with and without `--tab`, a duplicate name, and
  `--dry-run`.
- Live, in the test folder of the owner's Drive, which is shared with a person and not
  with the service account: an upload, a replace that keeps the file ID, and a
  spreadsheet created and then trashed by hand. These need the OAuth token, so they
  are run by the owner and recorded in the Log.

### Open questions

- **Whether `drive.file` is enough for a caller that only ever replaces files it
  uploaded itself.** It would narrow the grant for that caller. It needs a live check,
  like plan 004's.
- **Whether a replace should keep the old content as a revision.** Drive does by
  default for most file types. The command says nothing about revisions unless a
  caller needs to pin one.
- **Cleanup of live test files.** The package deletes nothing, so a live test leaves
  what it creates. One fixed file name, replaced on every run, keeps that to one file.
