# A live probe of Drive revisions

- Written: 2026-09-27
- Live API: the service account, read-only (`drive.readonly`), against the shared test
  spreadsheet, the shared test document, and one small binary file.
- Scope: step 6 (`gdrives revisions`) rests on how `revisions.list`/`.get` behave for a
  native Google file versus a binary one, and on what an export link actually returns,
  which the spec asked to be checked against the live API before relying on it. These
  are the findings the module, its fake-service tests, and the live test were written
  to. No package code was changed by the probe.

## Method

Each case below is one request against a file already shared with the service account,
read with the request the module now sends.

## Findings

**F1. `revisions.list` on a native Sheet or Doc returns only `id`, `mimeType`,
`modifiedTime` (and `kind`) with no `fields`.** `exportLinks`, `lastModifyingUser`, and
`published` come back only when the mask names them (or `fields="*"`). The mask
`nextPageToken,revisions(id,modifiedTime,lastModifyingUser(displayName,emailAddress),mimeType,size,keepForever,exportLinks)`
works on native and binary files alike: a native revision simply omits `size` and
`keepForever`, and a binary revision omits `exportLinks`. One call with that mask
returned HTTP 500 "Internal Error" once and succeeded on every repeat, so the module
retries `revisions.list` on 429 and 5xx (it is idempotent), reusing the existing Sheets
retry helper rather than a second backoff implementation.

**F2. Paging: `pageSize=1` returns one revision and a `nextPageToken`, oldest first.** A
native file's revisions are coalesced by Drive: a sheet written hundreds of times in one
day still listed only a handful of revisions. Revision IDs of native files are small
integers as strings; IDs of binary files are long opaque strings.

**F3. `revisions.get_media` on a native file fails with a 404 "Revision not found",
never with a "use export instead" error.** So the module decides binary versus native
from the file's own MIME type (`files.get`) before choosing which call to make, and
never falls back to export after a failed `get_media`.

**F4. An export link's key is a MIME type, and one export format can have two MIME
spellings** (an older and a newer string for the same download, e.g. the OpenDocument
Spreadsheet format). The module's extension-to-MIME table lists both spellings for a
format that has them, and either is treated as a match. A GET of the link itself (sent
with the service's own authorized HTTP client, following a redirect to a Google-hosted
download host) returns the format's bytes with a matching `content-type` and
`content-disposition`.

**F5. An old revision of a native file can be exported, not only the head.** A
document's first revision exported a few bytes of text and a later one far more; a
spreadsheet's revisions gave different bytes per revision. The head revision's export
equals what the ordinary whole-file export would give, which is why the module reads a
revision's own `exportLinks` rather than reusing the file-level export endpoint (which
only ever serves the head).

**F6. An export link's GET can fail with a body, not just a status.** After a burst of
fetches in a short time, a link answered with a 429 and an HTML page; an unauthenticated
fetch answered 401 with an HTML page. So the module checks the GET's status before
returning anything: a 200 is returned to the caller, a 429 or 5xx is retried with
backoff (export links are rate-limited separately from the rest of the API), and any
other non-200 raises a clear error naming the status. The (HTML) body of a failing
response is never written to the output — the write only ever happens after a 200.

**F7. `lastModifyingUser` for a revision made by a service account has an identical
`displayName` and `emailAddress`** (both the account's own address). For a person, the
two commonly differ. The module shows the pair once when they are equal, instead of
repeating the same string.

**F8. `acknowledgeAbuse` is refused on `revisions.get`'s metadata call** ("only
applicable for download requests") and is a no-op on an ordinary binary file's
`get_media`. The module leaves it out entirely: nothing here downloads a file abuse
scanning would flag differently than a normal read would.

**F9. A binary file's revision carries `size`, `keepForever`, and similar file-level
fields; `get_media` through the streaming downloader returns exactly that many bytes.**
The most recent revision's ID equals the file's own `headRevisionId`.

**F10. An unknown revision ID gives a 404 from `revisions.get`,** naming the ID in the
message — surfaced to the caller unchanged rather than reinterpreted.

**F11. Every finding above held on the `drive.readonly` scope alone;** nothing the
module needs requires a write scope.

## What this settles

- `list_revisions` sends the fields mask of F1 and retries `revisions.list` on 429/5xx.
- `download_revision` decides binary versus native from `files.get`'s MIME type first
  (F3), never from a failed `get_media`.
- A native download reads the specific revision's own `exportLinks` (F5) rather than the
  file-level export endpoint, and its extension-to-MIME lookup accepts either spelling
  of a two-MIME format (F4), refusing an extension the revision does not offer with the
  offered ones listed.
- The export-link fetch checks the status before returning bytes, retries 429/5xx, and
  never writes a non-200 body to disk (F6).
- `modified_by` collapses an identical display name and email to one string (F7).
- `acknowledgeAbuse` is not sent by any call the module makes (F8).
- The live test lists the shared test spreadsheet's revisions and downloads the newest
  as `.xlsx`, and does the same for a binary file when one is configured for the test
  environment (F2, F5, F9).
