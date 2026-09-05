---
id: 2
slug: docs-read-write
status: active
branch: feature/docs-read-write
created: 2026-09-03T11:33:48-07:00
concluded:
pr: https://github.com/gitronald/gdrives/pull/21
---

# Read and edit Google Docs content via the Docs API

## Plan

### Goal

Give Google Docs the same first-class, live treatment that
[001](../001-sheets-read-write/plan.md) gave Google Sheets: read a document's
content and write changes back in place — both as importable functions and as
`gdrives` CLI commands — using the **Docs API v1** (`documents.get` /
`documents.batchUpdate` / `documents.create`). Today a Doc is read-only from this
package and only as a whole file: `export` downloads a `.docx` (or `download`
auto-exports one) through the Drive API, and there is no way to change a word in
a Doc without round-tripping through a local Office file and a separate upload.
After this plan, `gdrives docs-*` does for Docs what `sheets-*` does for Sheets:
get / update / append / replace on the live document.

### Scope

In scope:
- `gdrives/docs.py` with read, overwrite, append, and find-and-replace helpers
  over `documents.*`.
- A `build_docs_service()` alongside `build_drive_service()` /
  `build_sheets_service()`.
- Write-capable auth for Docs (new scope), following 001's per-operation split —
  see **Key decision: scopes** below.
- Document targeting by URL, file ID, or Drive path (reuse the resolver pattern
  from `sheets.resolve_spreadsheet_id`).
- A plain-text view of a document (paragraphs, list items, table cells) for
  `docs-get` and for the read-locate-write commands.
- CLI commands mirroring the `sheets-*` set.
- Tests with a fake Docs service (mirror `FakeSheetsService` in
  `tests/helpers.py`) plus a service-account-gated live suite.

Out of scope (possible follow-ups):
- Formatting: text and paragraph styles, headings, fonts, images, page setup
  (`updateTextStyle`, `updateParagraphStyle`, `insertInlineImage`, ...). v1 is
  content only.
- Comments and suggestions. Comments live in the Drive API (`comments.*`), not
  the Docs API; suggestion-mode edits are a separate view of the same document.
- Headers, footers, and footnotes — read them if cheap, never write them in v1.
- Markdown rendering of a Doc (a plain-text flattening is enough here; see the
  `export` note below).
- Creating Docs inside a chosen folder and importing `.docx` files — both need a
  Drive write scope and belong with the broader write story seeded in
  [000](../000-mv-command/plan.md); listed as a stretch item below.

### Key decision: scopes (write access)

Reads need nothing new: `documents.get` accepts the existing `drive.readonly`
grant, so `docs-get` reuses `gdrives_token.json` exactly as `sheets-get` does.

Writes (`batchUpdate`, `create`) need `https://www.googleapis.com/auth/documents`
(or the broader `drive` / `drive.file`). Follow 001's pattern rather than widening
the shared default: add a `DOCS_WRITE_SCOPES` constant and pass it only from the
write entry points.

One wrinkle 001 did not have to face: `_token_path()` currently maps *every*
non-default scope set to the single `gdrives_token_rw.json`. A token consented
for `spreadsheets` does not carry `documents`, and google-auth will load it
anyway — the first Docs write would fail with a 403 instead of prompting for
consent, and re-consenting would clobber the Sheets grant. Two ways out:

- **Recommended — one token file per scope set.** Generalize `_token_path` to
  derive the filename from the scopes: the read-only default keeps
  `gdrives_token.json`, `SHEETS_WRITE_SCOPES` keeps `gdrives_token_rw.json` (no
  change for existing users), and any other set gets a stable, readable name
  (e.g. `gdrives_token_docs.json`). Also check the cached token's granted scopes
  on load and discard it when they do not cover the request, so a mismatch
  re-consents instead of 403ing.
- **Alternative — one combined write token.** Make every write command request
  `spreadsheets` + `documents` together. Simpler, but forces existing Sheets
  write users through a re-consent and over-grants each command.

Service-account and ADC paths already take `scopes=`; thread the new constant
through.

### The document model (what "content" means here)

`documents.get` returns a tree: `body.content` is a list of structural elements
(paragraphs, tables, section breaks, tables of contents); a paragraph holds
`elements` (text runs, inline objects, footnote references, page breaks), and
each carries `startIndex` / `endIndex` in **UTF-16 code units**. Every edit is
index-addressed, and an insertion or deletion shifts every index after it. Rules
to build on:

- **Flatten for reading.** `document_text(doc)` concatenates text-run content in
  order (each paragraph already ends in `\n`), walks table cells row by row
  (cells joined with tabs), and prefixes bulleted paragraphs (`paragraph.bullet`)
  with `- `. Non-text elements (inline images, footnote refs, page breaks) are
  skipped in the text but still occupy one index each — never reuse the
  flattener for index math.
- **Prefer index-free requests.** `insertText` with `endOfSegmentLocation`
  (append) and `replaceAllText` (find-and-replace) need no index bookkeeping.
  When a batch must contain index-addressed requests, order them from the
  largest index down so earlier edits never shift later ones.
- **Read-locate-write is guarded by revision.** Every `batchUpdate` built from a
  prior `get` sends `writeControl.requiredRevisionId` from that `get`, so the
  write fails cleanly if the document changed in between (the Docs equivalent of
  `sheets-set` refusing to rewrite the wrong row).
- **Tabs.** Docs can have multiple tabs; a plain `get` returns only the first
  tab's body unless `includeTabsContent=true`, and requests take an optional
  `tabId`. Default to the first tab and expose `--tab`, mirroring `sheets-*`.

### API surface (functions)

New module `gdrives/docs.py`. Core helpers take a Docs `service` and a
`document_id`:

- `pull_document(service, document_id, *, tabs=False) -> dict` — raw
  `documents.get`.
- `document_text(doc) -> str` — the plain-text flattening above.
- `body_range(doc) -> tuple[int, int]` — the editable span of the body (index 1
  up to, not including, the final newline, which the API refuses to delete);
  used by overwrite and clear.
- `append_text(service, document_id, text, *, tab_id=None) -> dict` —
  `insertText` at `endOfSegmentLocation`.
- `replace_text(service, document_id, find, replace, *, match_case=True) -> int`
  — `replaceAllText`; returns `occurrencesChanged`.
- `set_text(service, document_id, text, *, required_revision=None) -> dict` —
  delete the body range and insert `text` in one batch (overwrite).
- `clear_text(service, document_id, *, required_revision=None) -> dict` — the
  delete half alone.
- `create_document(service, title) -> str` — `documents.create`, returns the new
  ID (lands in the root of My Drive; moving it needs Drive write — stretch).
- `resolve_document_id(source, service=None) -> str` — URL / bare ID / Drive
  path, the twin of `resolve_spreadsheet_id` (factor the shared body into one
  helper if the two turn out identical).

Thin `run_*` entry points wrap each for the CLI, echoing `Document ID: ...` to
stderr like `_resolve_and_report`.

### CLI surface

Flat commands, matching `sheets-*` and `show-drives`; each wraps its logic in
`_cli_errors()` and lazy-imports its module:

- `gdrives docs-get <source> [--json] [-o out.txt]` — print the plain text (or
  the raw document JSON with `--json`); `-o` writes a file.
- `gdrives docs-update <source> --text-file body.txt [-y]` — overwrite the body
  with the file's text (confirm first; the whole body goes).
- `gdrives docs-append <source> (--text "..." | --text-file more.txt)` — insert
  at the end of the body.
- `gdrives docs-replace <source> --find "old" --replace "new" [--ignore-case]
  [--all]` — find-and-replace. Refuses when the phrase occurs more than once
  unless `--all`, counting occurrences from a prior `get`, so a targeted edit
  never rewrites the wrong sentence — the `sheets-set` refusal, in Docs form.
- `gdrives docs-clear <source> [-y]` — empty the body (confirm first).
- Stretch: `gdrives docs-create --title "..." [--text-file body.txt]` — a new
  Doc in My Drive root. Add `--parent <folder>` and a `.docx` import path only
  once a Drive write scope exists (plan 000).

`<source>` accepts a Doc URL, a bare file ID, or a Drive path, as for the Sheets
commands.

### `export` note

The Drive export endpoint also serves Google Docs as `text/plain` and (since
2024) `text/markdown`. Adding `.txt` and `.md` to `EXPORT_MIME_TYPES` is a
one-line, read-only win that complements `docs-get`; fold it in here if it fits,
otherwise leave it as a follow-up.

### Implementation order

1. **Auth**: `DOCS_WRITE_SCOPES`, `build_docs_service(scopes=...)`, per-scope-set
   token filenames with a granted-scope check; the read-only default stays
   byte-for-byte unchanged.
2. **Core module** `gdrives/docs.py`: `pull_document`, `document_text`,
   `body_range`, `append_text`, `replace_text`, `set_text`, `clear_text`,
   `resolve_document_id`, and the `run_*` entry points.
3. **CLI**: wire `docs-get` / `-update` / `-append` / `-replace` / `-clear` into
   `cli.py`.
4. **Tests**: a `FakeDocsService` in `tests/helpers.py` that tracks indices and
   `revisionId`, so shifted-index and stale-revision paths are unit-testable;
   `tests/test_docs.py` for each helper, the flattener (paragraphs, lists,
   tables, skipped elements), the resolver, and the refusal paths; CLI tests via
   the Typer runner. A live suite `tests/test_docs_integration.py` gated on a
   service account and a `GDRIVES_TEST_DOCUMENT_ID` env var (kept out of the
   code), each test appending and removing its own uniquely tagged paragraph.
5. **Docs**: `README.md`, `docs/setup-oauth.md` (new scope and token file), and
   the package-structure table plus Commands block in `.claude/CLAUDE.md`.
6. **Checks**: `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run pyrefly check`, and `uv run pytest` at 100% coverage before close.

### Open questions

- Token file naming: per-scope-set names as recommended, or the combined write
  token? Resolve at the auth step; either way `gdrives_token_rw.json` must keep
  working for Sheets.
- `docs-get` default output: plain text is the proposal. Is a `--markdown` view
  (via the export endpoint) worth having in the same command, or is
  `export -o file.md` enough?
- `docs-replace` case handling: default to case-sensitive (the API's
  `matchCase=true`) with `--ignore-case`, or the reverse?
- Does `docs-create` belong here at all, or should it wait for the Drive write
  scope so a new Doc can land in a folder from day one?

## Log

- 2026-09-04: activated on `dev`, branched `feature/docs-read-write` in a
  worktree, draft PR #21 opened.
- **Auth (step 1).** Took the recommended option: one token file per scope set.
  Known sets keep their historical names (`gdrives_token.json`,
  `gdrives_token_rw.json`); any other set derives a sorted name from each
  scope's last path segment, so the Docs write token is
  `gdrives_token_documents.json`. On load, the token JSON's granted `scopes`
  are checked against the request and a token that does not cover it is
  discarded (with a warning) so the flow re-consents instead of 403ing.
- **Core module (step 2)** shipped as specified, with these deviations:
  - `pull_document` always requests `includeTabsContent=true` (no `tabs=`
    flag); `tab_body` still accepts the legacy top-level `body` shape.
  - `set_text` / `clear_text` take `doc=` (an earlier `pull_document` result)
    instead of `required_revision=`: the body span and the revision guard must
    come from the same snapshot, so passing the snapshot is the coherent
    interface. `replace_text` keeps `required_revision=` since it is index-free.
  - Added `raw_text` / `count_occurrences` (undecorated text for occurrence
    counting, so the `- ` list prefix and tab-joined cells never count as
    matches), `resolve_tab_id` (title or ID), and `read_text_file` (drops one
    trailing newline for a clean round trip).
  - The shared resolver body became `resolve.resolve_file_id`;
    `sheets.resolve_spreadsheet_id` and `docs.resolve_document_id` are thin
    named wrappers.
  - `run_append` prefixes a newline when the body is non-empty, so the CLI
    appends a paragraph (the API's end-of-segment insert continues the last
    line).
- **CLI (step 3).** `docs-get` / `-update` / `-append` / `-replace` / `-clear`
  plus the stretch `docs-create` (lands in My Drive root, as its help says).
  Open questions resolved: plain text is the `docs-get` default with `--json`
  for the raw document (Markdown stays with `export -o file.md`);
  `docs-replace` is case-sensitive with `--ignore-case`; `docs-create` shipped
  without `--parent`.
- **`export` note.** `.txt` and `.md` added to `EXPORT_MIME_TYPES`.
- **Tests (step 4).** `FakeDocsService` holds plain text per tab, renders real
  indices, applies insert / delete / replace requests, bumps `revisionId`, and
  rejects a stale `requiredRevisionId` with a 400, so request ordering and the
  guard are tested end to end. The live suite is gated on
  `GDRIVES_TEST_DOCUMENT_ID` and never runs the whole-body operations against
  the shared document.
- **Checks.** ruff, ruff format, pyrefly, and pytest at 100% coverage
  (417 passed, 16 integration tests skipped without credentials).
