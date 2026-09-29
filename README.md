# gdrives v0.14.1a0

Command-line tools for Google Drive.

Browse Google Drives, list folder contents by path or ID, export Google Docs,
Sheets, and Slides to Office formats, download individual files or whole folder
trees, list and download a file's past revisions, rename and move files and
folders, read and write Google Sheet cell
ranges, keep a Sheet tab and a local file in step, read and edit Google Docs
content in place, and generate hyperlinked folder maps — all from the terminal.
Human-readable Drive paths (e.g. `My Drive/projects`) resolve against a local
drive-name cache, with first-class support for shared drives and "Shared with
me" items. Listings carry URL, type, modified-date, owner, and sharer columns,
and can be written as nested markdown or flat CSV from a single API traversal.
Folder downloads scan and summarize before prompting, auto-exporting
Google-native files and streaming binaries to disk with atomic writes.
Authenticates via OAuth, a service account, or Application Default Credentials,
requesting read-only Drive access (`drive.readonly`) by default; the Sheets,
Docs, and `mv` write commands opt into `spreadsheets` / `documents` / `drive`
scopes stored in separate tokens, so read-only users are never re-prompted.
Built on the
[Google Drive API v3](https://developers.google.com/drive/api/reference/rest/v3),
[Sheets API v4](https://developers.google.com/sheets/api/reference/rest), and
[Docs API v1](https://developers.google.com/docs/api/reference/rest) with a
Typer CLI.

## Project Structure

```
gdrives/
├── cli.py       # Typer CLI: ls, export, download, revisions, mv, upload, show-drives, sheets-*, docs-*
├── auth.py      # OAuth, service-account, and ADC authentication
├── drives.py    # Drive name→ID cache (fetch, save, resolve)
├── resolve.py   # Path→ID resolution (drive paths and "shared with me")
├── files.py     # Drive API wrappers: pagination, folder walk, file helpers
├── listing.py   # DriveEntry, recursive collection, and table/markdown/CSV formatters
├── export.py    # Export Google Docs, Sheets, and Slides to Office formats
├── download.py  # Download a single file, or recurse a folder, to local disk
├── revisions.py # List a file's revisions, and download one (read-only)
├── mv.py        # Rename and move files and folders (Drive API files.update)
├── upload.py    # Upload a local file, replacing a file of its name in place
├── local.py     # Local output: atomic writes, CSV formula escaping, terminal-safe names
├── sheets/      # Google Sheets: cell ranges, rules, and keyed sync (Sheets API v4)
│   ├── values.py     # spreadsheets.values.* wrappers, render options, and tab lookups
│   ├── retry.py      # Retry with exponential backoff and jitter
│   ├── cells.py      # Canonical cell strings, column types, row keys, and schema checks
│   ├── a1.py         # A1 notation and GridRange conversion
│   ├── match.py      # Keyed row updates (find_rows, set_by_match)
│   ├── rules.py      # Conditional format rules
│   ├── files.py      # Local CSV/TSV grids, and CSV/TSV/JSON record files
│   ├── table.py      # Read a whole tab as header-named, keyed records
│   ├── merge.py      # The pure three-way merge by row key
│   ├── apply.py      # Write a merge plan to a tab, guarded and read back
│   ├── order.py      # Put a keyed tab's rows in a given order by moving whole rows
│   ├── structure.py  # Add and delete columns, create tabs, set column widths
│   ├── create.py     # Create a native spreadsheet in a folder, and name its tabs
│   ├── config.py     # The sync config file (gdrives-sheets.json)
│   ├── stores.py     # Stores for the local side and the base (FileStore, JsonEntryStore, MemoryStore)
│   ├── sync.py       # Sync, pull, and push a config's tabs, and the report
│   └── commands.py   # run_* entry points for the sheets-* commands
└── docs.py      # Read and edit Google Docs content in place (Docs API v1)
```

## Installation

```bash
uv tool install gdrives
```

As a project dependency:

```bash
uv add gdrives
```

From GitHub instead of PyPI:

```bash
uv tool install git+https://github.com/gitronald/gdrives.git
# or, as a dependency: uv add git+https://github.com/gitronald/gdrives.git
```

From source (for development):

```bash
git clone https://github.com/gitronald/gdrives.git
cd gdrives
uv sync
```

The distribution and the installed command are both named `gdrives`.

## Setup

You need Google Drive API credentials before using `gdrives`. Pick the method
that fits, follow its steps, then run `gdrives show-drives` to verify. Every
method needs a Google Cloud project with the **Google Drive API** enabled; read
commands request read-only access (`drive.readonly`). The `sheets-*` commands
additionally need the **Google Sheets API** enabled, and the `docs-*` commands
the **Google Docs API**. The write commands request a write scope cached in its
own token so read-only access is never disturbed: `spreadsheets`
(`gdrives_token_rw.json`) for `sheets-update`, `sheets-append`, `sheets-clear`,
`sheets-set`, `sheets-add-rule`, and `sheets-delete-rule`; `documents` (`gdrives_token_documents.json`) for
`docs-update`, `docs-append`, `docs-replace`, `docs-clear`, and `docs-create`;
and the full `drive` scope (`gdrives_token_drive.json`) for `mv`, `upload`, and
`sheets-create`, the commands that change Drive itself.
When more than one is configured, authentication is attempted in order: OAuth,
then service account, then ADC.

### OAuth — personal use, interactive browser auth

1. At [console.cloud.google.com](https://console.cloud.google.com), create or select a project, then enable the **Google Drive API** under **APIs & Services → Library**.
2. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen): choose **External**, fill in app name and support email, leave it in **Testing**, and add your Google address under **Test users**.
3. **Credentials → Create Credentials → OAuth client ID**, application type **Desktop app**, then download the JSON.
4. Save it as `gdrives_credentials.json` in your config directory and export that path:
   ```bash
   export GOOGLE_CONFIG_DIR=~/.google   # directory holding gdrives_credentials.json
   ```
5. Run `gdrives show-drives`. A browser opens for one-time authorization; the token is cached to `$GOOGLE_CONFIG_DIR/gdrives_token.json` and reused (it re-auths automatically if revoked).
6. With no terminal attached (a tool that captures stdin), a command skips the consent. Run `gdrives login` to consent anyway: it prints the URL and waits (`--scope read|sheets|docs|drive`, `--timeout SECONDS`).

Full walkthrough: [docs/setup-oauth.md](docs/setup-oauth.md).

### Service account — automation, or sharing access with others

1. Create or select a project and enable the **Google Drive API** (as above).
2. **IAM & Admin → Service Accounts → Create Service Account**; give it a name like `drive-reader` and skip the optional grant steps.
3. Open the new account's **Keys** tab → **Add Key → Create new key → JSON**, then download it.
4. Save it as `service_account.json` in your config directory (or point `GOOGLE_SERVICE_ACCOUNT_PATH` at it):
   ```bash
   export GOOGLE_CONFIG_DIR=~/.google   # directory holding service_account.json
   # or: export GOOGLE_SERVICE_ACCOUNT_PATH=/path/to/key.json
   ```
5. **Share** the target folder or shared drive with the service account's email (e.g. `drive-reader@your-project.iam.gserviceaccount.com`) as **Viewer** — it can only see what's explicitly shared with it. Share as **Contributor** instead if you want to use `mv`, which writes.
6. Run `gdrives show-drives`. No browser flow.

Full walkthrough (key rotation, revoking access): [docs/setup-service-account.md](docs/setup-service-account.md).

### gcloud / ADC — simplest if you already use the gcloud CLI

1. Create or select a project and enable the **Google Drive API** (as above).
2. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install) if you don't have it.
3. Log in with the Drive scope — the `--scopes` flag is **required** (a plain login grants only `cloud-platform`, which excludes Drive and would 403):
   ```bash
   gcloud auth application-default login \
     --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
   ```
4. Set a quota project (use the project where you enabled the Drive API):
   ```bash
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```
5. Run `gdrives show-drives`. No `GOOGLE_CONFIG_DIR` and no credential files to manage.

Full walkthrough: [docs/setup-adc.md](docs/setup-adc.md).

## Configuration

Set via environment variables or a `.env` file, found in the directory you run
`gdrives` from or its nearest parent that has one (env vars take precedence):

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_CONFIG_DIR` | (required for OAuth; not needed for ADC) | Directory for the OAuth token and credentials |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | `$GOOGLE_CONFIG_DIR/service_account.json` | Service account key file |

Authentication tries OAuth first, then the service account, then Application
Default Credentials.

## CLI Commands

Run `gdrives show-drives` once to populate the drive-name cache
(`.gdrives/cache.json`); any command given a Drive path (`ls`, `download`, `mv`,
`upload`, and the `sheets-*` and `docs-*` commands) resolves it against the
cache.
`gdrives --version` prints the installed version.

### Log in

```bash
gdrives login                  # Consent to read access (every read command)
gdrives login --scope sheets   # The sheets-* write commands (also: docs, drive)
gdrives login --timeout 60     # Give up after 60 seconds (default 300)
```

Any command starts the consent it needs when run in a terminal. `login` starts
it with or without one: it prints the consent URL, waits for the browser to
come back, caches the token, and prints the credential the commands will now
use. When a cached token already serves the scope, nothing is asked. It exits 1
when the time runs out, or when the token could not be saved. It is also the
way to grant again after a token's refresh has failed. Before any command
waits on a consent or a token refresh, it says so on stderr with a line
starting `Credential:`. The line is also printed when OAuth is configured but no
cached token serves and there is no terminal for a consent, so the run goes on
as the service account or ADC; it then ends with the reason and `run gdrives login`.
It stays quiet when OAuth is not configured. `gdrives.auth.credential_line(info)`
is that line for a `CredentialInfo`, for a caller printing it by hand.

A caller that wants to say more can build on `gdrives.auth.describe_credentials()`,
whose `CredentialInfo` names why a credential was chosen without a network call:
`oauth_client` (the client secrets file, or `None` if OAuth is not configured),
`terminal` (whether a terminal is on stdin), `consent_skipped` (True when OAuth
is configured but no consent could run for lack of one), `service_account` (the
service account key file, if any), and `passed_over` (each cached OAuth token
file that was looked at and not used, as a `PassedToken(path, reason)` with
`reason` one of `PASSED_REASONS`: `missing`, `scopes`, `unreadable`, or
`invalid`). For example, to announce a fall back to the service account:

```python
from gdrives.auth import describe_credentials

info = describe_credentials()
if info.kind == "service_account" and info.consent_skipped:
    print(f"No terminal for OAuth consent ({info.oauth_client}); using {info}")
```

### List Drive contents

```bash
gdrives ls                                              # My Drive (default)
gdrives ls "My Drive/projects"                          # Subfolder
gdrives ls "Shared drive name/subfolder"                # Shared drive
gdrives ls "My Drive" --depth 2                         # Recurse deeper
gdrives ls --drive-id <folder-id>                       # By folder ID
gdrives ls --shared-with-me                             # Shared with me
gdrives ls "Shared folder" --shared-with-me             # Shared subfolder
```

### Save a listing as markdown or CSV

```bash
gdrives ls "My Drive/projects" --save-as map.md         # Nested markdown
gdrives ls "My Drive/projects" --save-as data.csv       # CSV export
gdrives ls "My Drive/projects" --depth 3 --save-as map.md
gdrives ls "My Drive/projects" --save-as map.md --save-as data.csv  # both, one traversal
```

File names come from whoever owns a file, so `ls` treats them as untrusted in
every output. A control character in a name (which could otherwise drive the
terminal) is shown as `\xNN` in the terminal table and in the saved CSV and
markdown alike, so viewing a saved listing with `cat` or `less` is safe too. In
a CSV, a cell starting with `=`, `+`, `-`, `@`, a tab, or a carriage return also
gets a leading `'` so that Excel or LibreOffice shows it as text instead of
running it as a formula. The markdown map otherwise keeps names as they are,
apart from markdown escaping.

### Export Google Docs, Sheets, and Slides

```bash
gdrives export <doc-url> -o output.docx     # Google Doc -> .docx
gdrives export <doc-url> -o output.md       # Google Doc -> Markdown (.txt for plain text)
gdrives export <sheet-url> -o output.xlsx   # Google Sheet -> .xlsx
gdrives export <sheet-url> -o output.csv    # Google Sheet -> .csv (first tab only)
gdrives export <slides-url> -o output.pptx  # Google Slides -> .pptx
```

### Read and write Google Sheet cell values

Operate on live cell ranges via the Sheets API — distinct from `export`, which
downloads a whole spreadsheet to a local file. The target is a Sheet URL, a bare
file ID, or a Drive path; the range is an A1 range like `Sheet1!A1:C10` (a bare
`A1:C10` targets the first tab).

```bash
gdrives sheets-get <sheet-url> "Sheet1!A1:C10"          # Print a range (aligned columns)
gdrives sheets-get <sheet-url>                          # First tab, whole used range
gdrives sheets-get <sheet-url> "A1:C10" --csv           # Comma-delimited to stdout
gdrives sheets-get <sheet-url> "A1:C10" -o out.csv      # Write CSV (or --tsv for TSV)
gdrives sheets-get <sheet-url> -o out.csv --escape-formulas  # Formula-like cells as text
gdrives sheets-update <sheet-url> "A1:C2" --values-file data.csv   # Overwrite a range
gdrives sheets-update <sheet-url> "A1" --values-file data.csv --raw  # Store literal strings
gdrives sheets-append <sheet-url> "Sheet1!A1" --values-file rows.csv  # Insert rows after the table
gdrives sheets-clear <sheet-url> "Sheet1!A1:C10"        # Clear values (prompts first; -y skips)
```

Reads use the read-only scope (no re-consent for existing users). The write
commands (`sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`, and the
rule commands below) request the `spreadsheets` scope the first time and cache it in a separate token. Cells
are plain strings on both sides: `--values-file` reads a local CSV, and by
default `USER_ENTERED` parses formulas, dates, and numbers like the Sheets UI
(`--raw` stores the literal text). Use `--raw` when writing data from an
untrusted source, so a leading `=`, `+`, `-`, or `@` is stored verbatim rather
than evaluated as a formula. `sheets-append` inserts its rows below the table,
pushing anything further down the tab out of the way rather than writing over
it. `sheets-get` prints values exactly as stored; add `--escape-formulas` when the
output is headed for a spreadsheet app, which prefixes a `'` to every cell
starting with `=`, `+`, `-`, `@`, a tab, or a carriage return (so `-5` becomes
`'-5`).

#### Update rows by lookup (`sheets-set`)

Find rows by column value(s) and set other columns on them — no A1 arithmetic.
Columns are addressed by **header name** (row 1). Repeat `--match` for a composite
(AND) key and `--set` for multiple columns:

```bash
# In the row where id=C300, set status=paid and amount=250
gdrives sheets-set <sheet-url> --match id=C300 --set status=paid --set amount=250

# Composite key: match on two columns before writing
gdrives sheets-set <sheet-url> -m year=2026 -m id=C300 -s status=paid

# Update every matching row (default refuses when >1 row matches)
gdrives sheets-set <sheet-url> -m status=pending -s reminder=sent --all

gdrives sheets-set <sheet-url> --tab Roster -m id=C300 -s status=paid  # non-default tab
```

By default it requires **exactly one** matching row — it refuses (listing the
rows) when the key is ambiguous, and errors when nothing matches, so a keyed
update never silently rewrites the wrong row. Pass `--all` to update every match.
`--raw` and the `USER_ENTERED` default apply as above. The Sheets API has no
revision check to tie a write to the read before it, so `sheets-set` reads the
tab again just before writing and refuses if the header or the matching rows
moved in between (a row inserted above the match, say).

#### Conditional formatting

List, add, and delete conditional format rules — e.g. to re-create the rules an
uploaded `.xlsx` loses when it is converted to a Google Sheet:

```bash
gdrives sheets-rules <sheet-url>                        # Rules by tab, each with its [index]
gdrives sheets-rules <sheet-url> --json > rules.json    # Raw rule dicts, for replay
gdrives sheets-add-rule <sheet-url> --range "Sheet1!A2:AA" \
    --formula '=OR($F2="Rejected", $F2="Inactive")' \
    --strikethrough --text-color "#999999"              # Custom-formula rule
gdrives sheets-add-rule <sheet-url> --rule-json rule.json   # Replay one captured rule
gdrives sheets-delete-rule <sheet-url> --tab Sheet1 --index 0  # Prompts first; -y skips
```

Rules on a tab form an ordered list and the first matching rule wins.
`sheets-add-rule` inserts at `--index` (default `0`, the top), and deleting a rule
shifts every later one up, so re-run `sheets-rules` before aiming another delete.
After you confirm a delete, the rules are read again, and the delete is refused
if a different rule now sits at that index.
`--range` is repeatable (all ranges must be on one tab). The format options are
`--bold`, `--italic`, `--strikethrough`, `--underline`, `--text-color`, and
`--background`, with colors as hex. `--rule-json` takes one rule object, or one
entry of the `sheets-rules --json` list (e.g. `jq '.[0]' rules.json`). Its ranges
keep their numeric `sheetId`, so replaying onto a different spreadsheet needs a
tab with that ID there.

Two API behaviors to know. First, an open-ended range like `A2:AA` is stored
clamped to the tab's current size, so it lists back as `A2:AA1000`. Second,
color-scale (gradient) rules show in `sheets-rules` and replay through
`--rule-json`, but the builder only makes custom-formula rules. `sheets-rules`
uses the read-only scope; the other two use the `spreadsheets` write scope.

### Sync a Sheet with a local file

Keep a tab of a Google Sheet and a local `.csv`, `.tsv`, or `.json` file in
step. A `gdrives-sheets.json` config, found in the working directory or a
parent, names **targets**: a spreadsheet and the tabs to keep in step, each
with a local file, a mode, and for a keyed sync the key columns:

```json
{
  "roster": {
    "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
    "tabs": {
      "Members": {"local": "data/members.csv", "key": ["member_id"]},
      "Summary": {"mode": "push", "local": "output/summary.csv"}
    }
  }
}
```

```bash
gdrives sheets-sync roster                         # Preview every sync tab of the target
gdrives sheets-sync roster --tab Members           # Preview one tab
gdrives sheets-sync roster --apply                 # Write sheet, local file, and base
gdrives sheets-sync roster --apply --adopt         # First sync: the local file wins
gdrives sheets-sync roster --apply --add-missing   # Add local columns the sheet lacks
gdrives sheets-sync roster --apply --drop-extra    # Delete sheet columns outside the projection
gdrives sheets-sync roster --apply --prefer local  # Resolve conflicts toward local
gdrives sheets-pull roster --apply                 # Replace local files for the pull tabs
gdrives sheets-push roster --apply                 # Replace the push tabs from local files
gdrives sheets-pull <sheet-url> --all-tabs -o out/ --apply  # Dump every tab, no config
gdrives sheets-pull <sheet-url> --all-tabs -o out/ --slug --bom --apply  # Slug file names, with a byte-order mark
gdrives sheets-widths <sheet-url> --tab Members    # Column widths as JSON, for a tab's "widths"
gdrives sheets-links <sheet-url> --color "#1155cc" # Check the links of URL cells on every tab
gdrives sheets-links roster --tab Members --apply  # A target's tab, in its link_urls colour, fixed
```

A `sync` tab is merged three ways by row key against a **base snapshot** (one
CSV per tab under `sheets-base/<target>/`, meant to be committed with the
local file): a cell edited on one side is written to the other, a cell edited
on both is reported as a conflict and left alone, and a new row on either side
is added to the other. Deleted rows are flagged, never deleted. A `pull` tab
replaces the local file with the tab, and a `push` tab replaces the tab's
values with the local file. A pull tab's `exclude` names columns to leave out
(the other way round from `columns`), so their values never reach the local
file or a report, even for a column added on the sheet later. A workbook kept
in one JSON file, `{"Members": [...], "Dues": [...]}`, is synced by giving each
tab an `entry` of its `.json` `local` file, and a target's `base_file` keeps
every sync tab's base as an entry of one `.json` file instead of one CSV per
tab. A tab reads a number as its value (`0.5` for a cell showing `50%`);
`render: "formatted"` reads every cell as the sheet displays it, for local
files that hold the displayed text. A tab's `strict_schema` makes it a problem
for a column of either side to have no `schema` entry, so a column added later
does not silently sync as text; `"local"` checks the local side only, and leaves
a sheet column outside the projection alone. A `schema` column's `present: true`
makes it a problem for the header to lack it, its `strict: true` narrows a
`bool` or `date` column to its one exact form (`TRUE`/`FALSE`, `YYYY-MM-DD`),
and a `str` column's `pattern` is a regular expression a non-blank cell must
match in full.

Every command previews by default and writes only with `--apply`. The report
goes to stdout, a preview that `--apply` would change ends with `Preview only;
rerun with --apply to write.` on stderr, and the exit code is 0 when in sync or applied, 1 for an
error, and 2 when conflicts, row flags, or held sheet values are left for a
person. A preview uses
the read-only scope; `--apply` first prints the credential it will use to
stderr, and `sheets-sync` and `sheets-push` then request the `spreadsheets`
write scope (`sheets-pull` writes only local files and stays read-only). Values
are synced, never formulas or formatting, and are written as literal strings.
See [docs/sheets-sync.md](docs/sheets-sync.md) for the config fields, the merge
and ownership rules, the first sync, and the exit codes.

The sheet links a URL as it is written. A `sync` or `push` tab's
`clear_links: true` leaves the cells a run writes with no link, and its
`link_urls`, `{"color": "#1155cc"}`, gives each URL cell a run writes a link to
its own text, in that colour, not underlined. `link_urls` never touches the
cells a person typed or pasted; `sheets-links` sweeps those, over every tab of
a spreadsheet or the tabs of a target, and with `--apply` fixes them (in
code, `sweep_url_links` and `format_sweep`). To audit a tab,
`linked_cells(..., detail=True)` also returns each link cell's `text` and
whether its link is a `HYPERLINK` formula's (`formula`), and `styled_cells`
finds the cells underlined or coloured as a link is that hold none.
`clear_link_format(..., style=True)` clears the underline and the text colour
with the link; a cell whose link comes from a `HYPERLINK` formula loses the
link and keeps the formula, which then shows its label as plain text. See
[links](docs/sheets-sync.md#links).

To read a tab into Python instead of a file, `pull_records(service,
spreadsheet_id, "Members", ...)` in `gdrives.sheets` takes a pull tab's
fields and hooks and returns the checked rows as `Records`, raising
`PullError` (with the tab's report) when the pull is refused or finds problems.
`decode_rows` turns the rows into typed values for a dataframe library of your
own. See [reading a tab into memory](docs/sheets-sync.md#reading-a-tab-into-memory).

A sync keeps the sheet's row order. To put a keyed tab back in an order of
your own, compute the order in Python, as the rows' keys first to last, and
call `reorder_rows` from `gdrives.sheets`. It moves whole rows, with their
formatting and every column, in the fewest moves, previews unless given
`apply=True`, and refuses an order that leaves out a row of the tab. See
[keeping a tab in order](docs/sheets-sync.md#keeping-a-tab-in-order).

A tab's `hooks`, or a target's for all its tabs, names checks of your own
for the commands to run, as `"module:function"`: `{"validate":
"roster_checks:known_status"}`, with `check`, `warn`, and `transform` beside
`validate`. **Running a command on such a config runs those functions**, a
preview included, so read a config from somewhere else before running it.
The module is found as `import` finds it, never beside the config, and every
name is checked before the first request. See
[hooks in the config](docs/sheets-sync.md#hooks-in-the-config).

A tab's `schema` may name a schema written in Python instead of giving one,
as `"module:attribute"`: `"schema": "clubtools.schema:MEMBERS"`, a mapping of
column name to `ColumnSchema`, or a function given the tab's title that
returns one. It is imported as a hook is, when a run starts and never when
the config is read, so **running a command on the config runs that module**,
a preview included. The schema found is checked as a `schema` object in the
config is, before the first request. See
[a schema in code](docs/sheets-sync.md#a-schema-in-code).

### Read and edit Google Docs content

Operate on the live document via the Docs API — distinct from `export`, which
downloads a whole Doc to a local file. The target is a Doc URL, a bare file ID,
or a Drive path. Commands act on the first tab unless `--tab` names another (by
title or ID) or the URL points at one (a link copied while viewing a tab carries
`?tab=`). `--tab` wins over the URL, and an empty `--tab ""` is an error rather
than the first tab.

```bash
gdrives docs-get <doc-url>                              # Print the text (lists as '- ', table rows tab-separated)
gdrives docs-get <doc-url> -o notes.txt                 # Write the text to a file
gdrives docs-get <doc-url> --json                       # Raw documents.get response (every tab)
gdrives docs-update <doc-url> --text-file body.txt      # Replace the whole body (prompts first; -y skips)
gdrives docs-append <doc-url> --text "New paragraph"    # Append a paragraph (or --text-file more.txt)
gdrives docs-replace <doc-url> --find "draft" --replace "final"   # Find and replace one occurrence
gdrives docs-replace <doc-url> --find "v1" --replace "v2" --all   # Replace every occurrence
gdrives docs-clear <doc-url>                            # Empty the body (prompts first; -y skips)
gdrives docs-create --title "Notes" --text-file body.txt  # New Doc in My Drive root; prints its URL
```

Reads use the read-only scope. The write commands (`docs-update`,
`docs-append`, `docs-replace`, `docs-clear`, `docs-create`) request the
`documents` scope the first time and cache it in a separate token. Content is
plain text on both sides: `docs-get` flattens paragraphs, list items, and table
rows, and the write commands insert plain paragraphs (formatting is out of
scope — use `export -o file.docx` for a styled copy). `docs-update` and
`docs-clear` leave plain `NORMAL_TEXT` paragraphs even when the body ended in a
heading or a list, and their prompt names the document and tab they replace.
`docs-replace` refuses
when the phrase occurs more than once unless `--all` is given, and errors when
it occurs nowhere, so a targeted edit never rewrites the wrong sentence
(`--ignore-case` relaxes matching). A phrase split by an image, chip, or table
cell is no match, and if the API replaces a different number of occurrences
than were counted, the command fails and says how many it replaced.
`docs-update`, `docs-clear`, `docs-append`, and `docs-replace` are tied to the
document revision they read, so a write is refused if someone changed the
document in between, including while a prompt waits.

### Download files and folders

```bash
gdrives download <file-url> -o ./out          # Single file -> ./out/<drive-name>
gdrives download <file-or-folder-id>          # By bare ID
gdrives download "My Drive/refs/paper.pdf"    # Single file by path -> ./paper.pdf
gdrives download "My Drive/refs"              # Whole folder (recurses by default)
gdrives download "My Drive/refs" --depth 1    # Folder, flat (no recursion)
gdrives download "My Drive/refs" -y           # Skip the confirmation prompt
gdrives download "My Drive/refs" -y --skip-existing  # Resume: fetch only what is missing
```

A source that resolves to a single file downloads immediately under its Drive
name. A folder is scanned first, showing a summary, then prompts before
downloading (`--depth` only affects folders). Google Docs, Sheets, and Slides
auto-export to `.docx` / `.xlsx` / `.pptx`; other Google-native types (Forms,
Drawings, etc.) are skipped. A name that is already taken locally gets a
` (1)` suffix instead of being overwritten.

One file that fails does not stop a folder download. That covers a Doc too
large to export, a download-restricted file, or a name the local filesystem
rejects. The rest still download, and the failures are listed at the end with
exit status 1. Rerun with `--skip-existing` to pick up where it stopped. Each
entry maps to the same local path it got the first time, and entries already
there are skipped instead of saved again as ` (1)` copies. Control characters
in Drive names are replaced with `_` in local file names.

### List and download a file's revisions

```bash
gdrives revisions <file-url>                                     # List revisions (id, modified time, by, size)
gdrives revisions "My Drive/refs/paper.pdf"                       # By path
gdrives revisions <file-url> --json                               # Raw revision list, as JSON
gdrives revisions <sheet-url> --download <revision-id>            # Download one revision into ./
gdrives revisions <sheet-url> --download <revision-id> -o out.csv # ...to a chosen path
gdrives revisions <sheet-url> --download <revision-id> --format csv -o out/  # ...into a directory, as CSV
```

Read-only: this lists and downloads revisions but never restores, pins
(`keepForever`), or deletes one, and it never touches Drive's write scopes.
A binary file downloads its stored bytes as-is; a Google Doc, Sheet, or
Slides file is fetched from that revision's own export links (an older
revision, not just the latest, since Drive keeps them), in the format named
by `--format` (an extension such as `xlsx`, `csv`, or `pdf`; default the
type's usual export) — `--format` is refused for anything else. `-o` takes a
file path or a directory; for a directory the local name is the file's name,
the revision ID, and the format's extension.

### Rename and move files and folders

```bash
gdrives mv "My Drive/notes.txt" "renamed.txt"               # Rename in place
gdrives mv "My Drive/notes.txt" "My Drive/archive"          # Move into an existing folder
gdrives mv "My Drive/notes.txt" "My Drive/archive/new.txt"  # Move and rename in one call
gdrives mv "My Drive/notes.txt" "My Drive/archive" --dry-run   # Print the change, make none
gdrives mv --source-id <file-id> --dest-id <folder-id> --name new.txt  # Skip path resolution
```

Like Unix `mv`, the destination decides the operation: a bare name (no `/`)
renames in place, a path that resolves to an existing folder moves the item into
it under its current name, and a path whose parent folder exists but whose final
segment does not does both in a single `files.update` call. `--dry-run` resolves
everything and prints the intended change without writing, so it stays on the
read-only scope.

It changes Drive itself, and it needs the full `drive`
scope — `drive.readonly` cannot call `files.update` — so any `mv` without
`--dry-run` authorizes that scope into its own `gdrives_token_drive.json`, even
if the move turns out to be a no-op. Two moves are refused
rather than guessed at: one whose item has several parent folders (Drive allows
that, and `mv` will not choose which one to detach from), and one that crosses
drives, which `files.update` cannot do; a folder is also refused as a
destination for itself or for one of its own descendants. Drive permits
duplicate names within a folder, so renaming onto a name already in use is
allowed.

Under a **service account**, the `drive` scope is not enough on its own: the
file or folder must also be shared with the service account as **Contributor**
or higher. Viewer is read-only and `files.update` fails with a 403 no matter
which scope was granted.

### Upload a file

```bash
gdrives upload report.pdf "My Drive/reports"             # Into a folder, under the local name
gdrives upload out.pdf "My Drive/reports/report.pdf"     # Into a folder, under another name
gdrives upload report.pdf "My Drive/reports" --dry-run   # Print the operation, make none
gdrives upload report.pdf --dest-id <folder-id> --name q3.pdf  # Skip path resolution
gdrives upload report.pdf --file-id <file-id>            # Replace that file's content
gdrives upload notes.md "My Drive/notes" --mime-type text/markdown  # Set the type
```

One local file is uploaded per run. As for `mv`, the destination decides where
it goes: a path that resolves to an existing folder takes the file under its
local name, and a path whose parent folder exists but whose final segment does
not takes it under that name. A path with no `/` is a drive, and means its root.

What the folder already holds under that name decides the operation. Names
compare without regard to case, as they do when a path is resolved.

- **One file**: its content is replaced in place, so its ID and every link to
  it stay the same. Its name and its folder are not touched.
- **No file**: the file is created.
- **Several files**: refused, with each one's ID listed. Name the one to
  replace with `--file-id`.
- **A Doc, Sheet, or Slides file**: refused, since an upload cannot replace the
  content of a Google-native file.

A file or a folder in the trash is refused when `--file-id` or `--dest-id`
names it; a path never resolves to one.

After the write, the file is read back and its size and MD5 checksum are
compared with the local file's; a difference exits 1 and names the file. The
file's URL is printed to stdout, and its ID and the operation to stderr. The
MIME type is guessed from the local file's extension unless `--mime-type` sets
it, and nothing is converted: an `.xlsx` stays an `.xlsx`. The upload is
resumable, in chunks of 8 MiB, each sent again up to five times after a server
error or a dropped connection. Nothing is deleted. Drive keeps the replaced content as a revision
by default (see `gdrives revisions`), and the command pins none.

`upload` needs the full `drive` scope, the one `mv` uses, cached in the same
`gdrives_token_drive.json`. `--dry-run` resolves everything and prints the
operation (`create` or `replace`, the file's name and ID, its size and type)
on the read-only scope. Under a **service account**, the folder or the file
must be shared with it as **Contributor** or higher. A service account has no
storage of its own, so a file it creates in a folder of someone's My Drive may
be refused for lack of quota; a shared drive, or replacing a file someone else
owns, does not depend on it.

From code, `upload_file` plans, writes, and reads back in one call, and returns
the file's metadata:

```python
from gdrives.auth import DRIVE_WRITE_SCOPES, build_drive_service
from gdrives.upload import upload_file

drive = build_drive_service(DRIVE_WRITE_SCOPES)
meta = upload_file(drive, "report.pdf", folder_id="<folder-id>")
print(meta["id"], meta["md5Checksum"])
```

### Create a spreadsheet

```bash
gdrives sheets-create --title "Roster"                            # In the root of My Drive
gdrives sheets-create --title "Roster" --folder "My Drive/clubs"  # In a folder, by path
gdrives sheets-create --title "Roster" --folder-id <folder-id>    # In a folder, by ID
gdrives sheets-create --title "Roster" --tab Members --tab Dues   # Name its tabs
gdrives sheets-create --title "Roster" --folder "My Drive/clubs" --dry-run  # Create nothing
gdrives sheets-create --from book.xlsx --folder "My Drive/clubs"  # From a local workbook
gdrives sheets-create --from members.csv --title "Roster" --folder-id <folder-id>
```

Creates a native Google Sheet and prints its URL to stdout, and its ID to
stderr. The URL or the ID is what a target's `spreadsheet` in
`gdrives-sheets.json` takes, so a first `sheets-push` has a spreadsheet to write
to.

A new spreadsheet has one tab. With `--tab`, that tab is renamed to the first
title given and the others are added after it, in order, so nothing is deleted.
Without `--tab` the tab is left as it is. Drive permits duplicate names, so a
file of the same name in the folder is not an error: the command says so on
stderr, with the ID of each, and creates the spreadsheet. `--dry-run` reports
the same and creates nothing, on the read-only scope. A `--folder-id` that
names a folder in the trash is refused.

`--from` starts the spreadsheet from a local `.xlsx` or `.csv` file (by
extension, in any case; anything else is refused before a request) instead of
an empty one. The file is uploaded as the content of the same `files.create`,
with the spreadsheet MIME type in the metadata, and Drive converts it. The
upload is the resumable one `upload` makes, with each chunk retried. `--title`
defaults to the file's stem, and `--tab` is refused, since the workbook names
its own tabs. `--dry-run` prints the file, its size, and the folder, and
uploads nothing. A converted file has no size or checksum to compare with the
local file's, so the read-back checks its type instead: when Drive left the
upload unconverted, the command fails naming the file's ID, since the file
exists either way. What Drive makes of a formula, a date, or a merged cell is
Drive's conversion, and is not something the command controls.

The file is created through the Drive API, since the Sheets API creates in the
root of My Drive only, so `sheets-create` needs the full `drive` scope, cached
in `gdrives_token_drive.json`; `spreadsheets` alone cannot place a file in a
folder. From code:

```python
from gdrives.auth import DRIVE_WRITE_SCOPES, build_drive_service, build_sheets_service
from gdrives.sheets import create_spreadsheet

drive = build_drive_service(DRIVE_WRITE_SCOPES)
sheets = build_sheets_service(DRIVE_WRITE_SCOPES)
spreadsheet_id = create_spreadsheet(
    drive, sheets, "Roster", folder_id="<folder-id>", tabs=["Members", "Dues"]
)

# From a local workbook: the file is converted, and names its own tabs
spreadsheet_id = create_spreadsheet(
    drive, sheets, "Roster", folder_id="<folder-id>", source="book.xlsx"
)
```

### Show available drives

```bash
gdrives show-drives
```

Output includes URL, type (personal/shared), name, and ID for each accessible
drive, and is cached to `.gdrives/cache.json` for use by the other commands.

## Development

```bash
uv sync --all-groups                    # install with dev tools
uv run pytest                           # all tests, with coverage
uv run pytest -m "not integration"      # unit tests only
uv run ruff check . && uv run pyrefly check
```

The unit tests run against fake Drive, Sheets, and Docs services, so they need no
credentials, and they must keep line and branch coverage at 100%.

### Live integration tests

The tests marked `integration` call the real Sheets and Docs APIs, to catch what
the fakes can't: request-shape mismatches, scope problems, and how the API
actually stores things. They need your own throwaway files, and they **skip**
rather than fail when those aren't configured. A run that skips them ends with a
"live integration tests skipped" note naming what is missing.

1. Set up a **service account** (see [Setup](#service-account--automation-or-sharing-access-with-others)).
   The live tests always use it, even when OAuth is configured.
2. In the service account's Google Cloud project, enable the **Google Sheets API**
   and the **Google Docs API**.
3. Create a throwaway **Google Sheet** and a throwaway **Google Doc**. Share each
   one with the service account's email (`client_email` in its key file) as
   **Editor**.
4. Put their IDs (the long string in each file's URL) in a `.env` file at the
   repo root. It is gitignored, so the IDs stay out of version control:

   ```bash
   GDRIVES_TEST_SPREADSHEET_ID=<sheet id>
   GDRIVES_TEST_DOCUMENT_ID=<doc id>
   ```

5. Run `uv run pytest -m integration`. The Sheets API allows 60 reads and 60
   writes a minute, and the tests wait when it refuses a request, so a run takes
   from half a minute to a few minutes.

The tests leave your files as they found them. The Sheets tests share one
temporary `itest_<hex>` tab, which is added before the first test, emptied
between tests, and deleted after the last. Each Docs test appends a
uniquely tagged paragraph and removes it. The whole-body Docs writes, which would
wipe a tab, run only on a temporary `itest_<hex>` tab that the test adds to the
document and then deletes.

## Related projects

There are a few options out there, but most haven't been touched in years, and none did the mapping tasks implemented here.

- [PyDrive2](https://github.com/iterative/PyDrive2) — fork of PyDrive, high-level Google Drive wrapper
- [PyDrive](https://github.com/googlearchive/PyDrive) — the original high-level wrapper, now archived
- [gdrive](https://pypi.org/project/gdrive/) "Simple Google Drive wrapper to traverse files"
- [gdriver](https://pypi.org/project/gdriver/) "Actually usable Google Drive client"
- [drive](https://pypi.org/project/drive/) "Google Drive client"

## Security & privacy

- Read commands request **read-only** Drive access (`drive.readonly`) and never modify or delete anything in your Drive. Only the Sheets write commands (`sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`, `sheets-add-rule`, `sheets-delete-rule`, and `sheets-sync` and `sheets-push` with `--apply`), the Docs write commands (`docs-update`, `docs-append`, `docs-replace`, `docs-clear`, `docs-create`), and `mv`, `upload`, and `sheets-create` request write access, via the `spreadsheets`, `documents`, and `drive` scopes respectively; a read command never loads or requests them. `mv` renames and reparents only the one item you name, `upload` writes only the one file it names, and `sheets-create` only adds a file — none of them deletes anything, and each stays on the read-only scope with `--dry-run`, as do `sheets-sync` and `sheets-push` without `--apply` and `sheets-pull` with or without it.
- The cached OAuth tokens (`$GOOGLE_CONFIG_DIR/gdrives_token.json` for read-only, `gdrives_token_rw.json` for the Sheets write scope, `gdrives_token_documents.json` for the Docs write scope, `gdrives_token_drive.json` for the Drive write scope used by `mv`) hold long-lived refresh tokens and are written with owner-only `0600` permissions. Each scope set has its own token file so requesting one kind of write access never clobbers or re-consents another. A cached token whose grant does not cover a request is re-authorized rather than reused, and one whose grant is broader (a `drive` token, for a Sheets write) is used as it is. The names `gdrives_token*.json` are reserved: a consent never overwrites a token file holding a grant the new one does not include, and writes its token under a name derived from its scopes instead (see [docs/setup-oauth.md](docs/setup-oauth.md)). Keep `gdrives_credentials.json` and `service_account.json` out of version control and shared locations.
- `gdrives show-drives` writes `.gdrives/cache.json` with the names and IDs of every Drive you can access; it is gitignored by default — keep it out of shared locations.
- Names of shared items are chosen by other people. `ls`, `download`, `mv`, and `show-drives` escape control characters in them before printing, so an embedded escape sequence can't rewrite the terminal, and `ls --save-as` CSVs prefix formula-like cells with `'` so a spreadsheet app won't run them.
- Local files are written through a private temporary file and renamed into place, so an interrupted run never leaves a partial download, export, listing, or drive cache. Overwriting an existing file (`export -o`, `docs-get -o`) keeps that file's permissions.
