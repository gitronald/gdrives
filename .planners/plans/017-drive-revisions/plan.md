---
id: 17
slug: drive-revisions
status: retired
branch: null
created: 2026-09-27T16:46:21-07:00
concluded: 2026-09-27T17:04:35-07:00
pr: null
---

# List and download a file's Drive revisions, read-only

## Plan

### Goal

Read-only access to a file's Drive revisions: list them, and download one to a local
path. A downstream project recovers values from old revisions of a spreadsheet after a
bad sync by calling `service.revisions()` directly. This gives it a supported wrapper
and a command.

### Hard rule

The module calls `revisions.list` and `revisions.get` only (plus `files.get` for the
file's MIME type, and a GET of an export link). It never calls `update`, `delete`, or
anything that restores a revision. Everything runs on the read-only default scope
(`drive.readonly`). A test runs every public function against a fake service that
raises on any other method.

### Module `gdrives/revisions.py`

- `Revision`, a frozen dataclass with these fields:
  - `id`
  - `modified_time` (the API's RFC 3339 string, kept as given)
  - `modified_by` (display name, and the email address when the API gives one)
  - `mime_type`
  - `size` (binary files only)
  - `keep_forever`
  - `export_links` (native files only)
- `list_revisions(service, file_id) -> list[Revision]` pages through `revisions.list`
  (`pageSize` 1000 and `nextPageToken`) with an explicit `fields` mask, oldest first as
  the API returns them.
- `download_revision(service, file_id, revision_id, output, *, mime_type=None) -> Path`
  - Gets the file's `mimeType` first.
  - A file stored as-is (not `application/vnd.google-apps.*`) is fetched with
    `revisions.get_media` through `MediaIoBaseDownload`, as `download.py` fetches a
    file.
  - A native Google file cannot be fetched that way. It is fetched from the revision's
    `exportLinks`, in the format named by `mime_type`. The default is the one
    `NATIVE_EXPORTS` gives the type (`.xlsx` for a Sheet, `.docx` for a Doc, `.pptx`
    for Slides). The link is fetched with the service's own authorized HTTP client. A
    format the revision does not offer is refused, with the offered ones listed.
  - `output` is a file path, or a directory. For a directory, the name is the file's
    name plus the revision id plus the format's extension.
  - The bytes go through `atomic_output`, as every other download does, so a failed
    fetch leaves no partial file.

### Check the live API before trusting any of this

The behavior above is from the API reference and the request, not from a run. Before
the code is final, probe the live API with the test service account on the shared test
spreadsheet and doc, and a binary file:

- whether `revisions.list` on a native Sheet returns `exportLinks`, and under which
  `fields`
- whether `revisions.get_media` on a native file fails, and how
- what an export link returns
- whether an old revision of a native file can be exported at all, or only the head
- whether `lastModifyingUser` comes back for a service account
- what `revisions.get` with `acknowledgeAbuse` needs, if anything

Record the findings in an `implementation-notes/` sidecar of this plan, and let them
decide the design where they differ from it.

### CLI

- `gdrives revisions FILE` lists revisions as aligned columns (id, modified time,
  modified by, size), with `--json` for the raw list. `FILE` is a URL, an ID, or a Drive
  path, as for `download`.
- `gdrives revisions FILE --download REVISION_ID [-o PATH] [--format EXT]` downloads
  one revision. `--format` is an extension such as `xlsx`, `csv`, or `pdf`, looked up
  in the revision's export links, and applies to native files only.
- Names shown in the terminal go through `printable`.

### Tests

- Unit tests with the fake service cover:
  - listing with paging
  - a binary download
  - a native export by the default format and by `--format`
  - an unknown format
  - a directory output
  - an atomic write failing midway
  - the forbidden-method guard
- CLI tests with `CliRunner`.
- A live test, skipped without the test environment, that lists the revisions of the
  test spreadsheet and downloads the newest as `.xlsx`, and does the same for a binary
  file if the test setup has one.

### Docs

- README command list and a short section, and `.claude/CLAUDE.md`'s command list and
  package structure.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Restoring, pinning (`keepForever`), or deleting revisions.
- Diffing two revisions.

## Log

### 2026-09-27

- Retired unimplemented: folded into [plan 018](../018-downstream-adoption-gaps/plan.md) as step 6 ([spec](../018-downstream-adoption-gaps/subplans/6-drive-revisions.md)). The spec above is kept as drafted.
