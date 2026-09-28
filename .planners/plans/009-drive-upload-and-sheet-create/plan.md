---
id: 9
slug: drive-upload-and-sheet-create
status: done
branch: feature/drive-upload-and-sheet-create
created: 2026-09-27T11:03:58-07:00
concluded: 2026-09-27T21:03:52-07:00
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

## Log

### 2026-09-27: implementation

Both commands are in, on one branch and one PR.

Commits:

- `41bbc61` add upload command and gdrives.upload
- `dbad2ea` add sheets-create command and create_spreadsheet
- `39b5214` document upload and sheets-create
- `9221bae` retry upload chunks and refuse trashed targets
- `b8fe7f7` fix a test that built a real drive service

What was built:

- `gdrives/upload.py`: `check_arguments`, `plan_upload` (an `UploadPlan`, `create` or
  `replace`), `apply_upload`, `verify`, `upload_file`, and `run`.
- `gdrives/sheets/create.py`: `create_spreadsheet`, `name_tabs`, and `check_tabs`, with
  `run_create` in `gdrives/sheets/commands.py`.
- `gdrives/files.py`: `find_named`, the files of one name in a folder, and `get_folder`,
  the folder a write goes in. Both commands use both.
- `gdrives/mv.py`: `resolve_destination` keeps the bare-name branch and hands the path
  to a new `resolve_folder`, which `upload` shares.
- `tests/helpers.py`: `FakeDriveFiles`, a Drive fake that holds files with their content
  and answers `files.list`, `files.create`, and `files.update` with a media body. The
  fake in `tests/test_mv.py` is left as it is, since its tests depend on how it answers.

Decisions where the plan left room:

- **Exactly one of `DEST`, `--dest-id`, or `--file-id` names the target.** The plan has
  `--file-id` naming one of several matches. It is an alternative to the path, as `mv`'s
  by-ID flags are, and not a modifier of it: a replace by ID needs no folder.
- **`--name` was added**, to go with `--dest-id`. Without it a create by folder ID could
  only take the local file's name.
- **Names compare without regard to case**, as path resolution compares them. A folder
  of the same name is not a match.
- **The URL goes to stdout, and the ID and the operation to stderr**, as `docs-create`
  does, so a script reads the URL alone.
- **`sheets-create` prints the ID before it names the tabs**, so a failure there still
  names the file that was created.
- **The first tab is found by reading the new spreadsheet**, not assumed to be `Sheet1`,
  since its title follows the account's language.
- **No retry on the Drive `files.create`.** No other Drive call in the package retries.
- **No automated live test.** The live suite runs as the service account, which the
  test folder is not shared with, so the writes are checked by hand, as planned.

Checked against the API, read-only, with `--dry-run`:

- An upload into the test folder plans a `create`.
- An upload under the name of a spreadsheet there is refused as Google-native.
- `sheets-create` under the name of a file there notes the file and would create.
- A `files.list` query of `name = '...'` finds a file whose name differs by case, so
  the server compares without regard to case and the filter in `find_named` agrees
  with it.

The requests of a create, a replace, and a spreadsheet create were also built with the
client library and not sent, to confirm their method, their URL, and their body.

### 2026-09-27: review follow-up

A review of the PR at the medium level: two finders, three verifiers, and a sweep.
It is posted on the PR.

Actioned, each with tests:

- **The resumable upload never retried a chunk**, so the first dropped connection
  ended the run, against Design 1. Each chunk is sent with `num_retries` of 5, in
  chunks of 8 MiB where the client library's default is 100 MiB.
- **A target in the trash, named by ID, was written to without a word.** `--file-id`,
  `--dest-id`, and `sheets-create --folder-id` refuse one.
- **The folder check existed twice.** Both commands call `get_folder`.
- **`patch_drive_service` duplicated `tests/test_mv.py`'s `patch_service`.** That file
  imports the shared one.

Conscious no-ops:

- **`verify` reads the file back with its own `files.get`**, though the write's
  response carries the same fields. Design 1 asks for the read-back.
- **`check_tabs` runs twice on one path.** `name_tabs` is public and checks its input.
- **Two fakes of the Drive `files` resource.** They answer differently, and the tests
  of `mv` depend on how theirs does.
- **Two uploads at once of one name both create.** Drive has no precondition for a
  create and permits duplicates. A later run refuses the pair and lists both.

Found by CI, not by the review: a test called `resolve_folder` with no service, so
path resolution built a real one. It passed where credentials are configured and
failed in CI from the first commit of the branch. It now passes a stand-in.

### 2026-09-27: live checks

Run in the test folder of the owner's Drive, on the OAuth token of the `drive` scope,
with the branch's code.

- **Upload, create.** A 4-byte text file was created in the folder, and read back
  with the local file's size and checksum.
- **Upload, replace.** The same local file with new content, 12 bytes, replaced the
  first in place. The file ID and the URL printed were the ones of the create.
- **Spreadsheet.** `sheets-create --title ... --folder ... --tab A --tab B` created a
  spreadsheet in the folder, and its tabs read back as `A` and `B`, in that order.

The first attempt wrote nothing. The cached token of the `drive` scope belonged to
another account than the read-only token, so every `--dry-run` passed and every write
was refused at path resolution, as a folder not found. The two
were told apart by `about.get` and the ID of `root`. After a `login --scope drive` as
the account that holds the folder, the three checks passed.

Left in the test folder, to trash by hand: one text file and one spreadsheet. The
upload's file is the one a later run replaces.

One more commit after the checks: `caab4fe` name upload and sheets-create in login
help.

### Open questions, as they stand

- **Whether `drive.file` is enough for a caller that only replaces its own uploads.**
  Not checked. Both commands request `drive`.
- **Whether a replace keeps the old content as a revision.** Not checked live. The
  command pins nothing and says nothing of revisions.
- **Cleanup of live test files.** As planned: one fixed file name, replaced on every
  run. A spreadsheet is added by every run of its check, so that one stays by hand.

## Retrospective

- **The plan held.** Both commands were built as designed. What changed was added at
  the edges: `--name`, the refusal of a trashed target, and the retries.
- **"Resumable" was a claim before it was a behavior.** A resumable request with no
  retries fails at the first dropped connection like any other. The fakes could not
  show it and neither finder raised it; it came from reading the design's sentence
  against the code that was meant to make it true.
- **A dry run on another scope proves nothing about the write.** The read-only and
  the write token were two accounts, so the preview and the write saw two Drives.
  Before a live write, compare the accounts behind the scopes.
- **A test that passes where credentials are configured is not a passing test.** One
  built a real service and failed in CI from the first push. CI was first looked at
  when the review was done, several pushes late. Look at it after the first push.
- **What the plan left open was settled against the API, not reasoned about.** Whether
  `name =` compares case took one read-only query to answer, where a verifier without
  the network could only call it plausible.
- **Sharing beat copying twice.** `resolve_folder`, `find_named`, and `get_folder`
  each began as code in one command that the other needed, and the guard added to one
  (the trash) reached the other for nothing.
