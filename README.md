# gdrives v0.6.1a0

Command-line tools for Google Drive.

Browse Google Drives, list folder contents by path or ID, export Google Docs,
Sheets, and Slides to Office formats, download individual files or whole folder
trees, read and write Google Sheet cell ranges, and generate hyperlinked folder
maps — all from the terminal.
Human-readable Drive paths (e.g. `My Drive/projects`) resolve against a local
drive-name cache, with first-class support for shared drives and "Shared with
me" items. Listings carry URL, type, modified-date, owner, and sharer columns,
and can be written as nested markdown or flat CSV from a single API traversal.
Folder downloads scan and summarize before prompting, auto-exporting
Google-native files and streaming binaries to disk with atomic writes.
Authenticates via OAuth, a service account, or Application Default Credentials,
requesting read-only Drive access (`drive.readonly`) by default; the Sheets
write commands opt into a `spreadsheets` scope stored in a separate token, so
read-only users are never re-prompted. Built on the
[Google Drive API v3](https://developers.google.com/drive/api/reference/rest/v3)
and [Sheets API v4](https://developers.google.com/sheets/api/reference/rest)
with a Typer CLI.

## Project Structure

```
gdrives/
├── cli.py       # Typer CLI: ls, export, download, show-drives
├── auth.py      # OAuth, service-account, and ADC authentication
├── drives.py    # Drive name→ID cache (fetch, save, resolve)
├── resolve.py   # Path→ID resolution (drive paths and "shared with me")
├── files.py     # Drive API wrappers: pagination, folder walk, file helpers
├── listing.py   # DriveEntry, recursive collection, and table/markdown/CSV formatters
├── export.py    # Export Google Docs, Sheets, and Slides to Office formats
├── download.py  # Download a single file, or recurse a folder, to local disk
└── sheets.py    # Read and write Google Sheet cell ranges (Sheets API v4)
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
commands request read-only access (`drive.readonly`). The Sheets write commands
(`sheets-update`, `sheets-append`, `sheets-clear`) additionally need the
**Google Sheets API** enabled and request a `spreadsheets` write scope, cached
in a separate token (`gdrives_token_rw.json`) so read-only access is never
disturbed. When more than
one is configured, authentication is attempted in order: OAuth, then service
account, then ADC.

### OAuth — personal use, interactive browser auth

1. At [console.cloud.google.com](https://console.cloud.google.com), create or select a project, then enable the **Google Drive API** under **APIs & Services → Library**.
2. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen): choose **External**, fill in app name and support email, leave it in **Testing**, and add your Google address under **Test users**.
3. **Credentials → Create Credentials → OAuth client ID**, application type **Desktop app**, then download the JSON.
4. Save it as `gdrives_credentials.json` in your config directory and export that path:
   ```bash
   export GOOGLE_CONFIG_DIR=~/.google   # directory holding gdrives_credentials.json
   ```
5. Run `gdrives show-drives`. A browser opens for one-time authorization; the token is cached to `$GOOGLE_CONFIG_DIR/gdrives_token.json` and reused (it re-auths automatically if revoked).

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
5. **Share** the target folder or shared drive with the service account's email (e.g. `drive-reader@your-project.iam.gserviceaccount.com`) as **Viewer** — it can only see what's explicitly shared with it.
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

Set via environment variables or a `.env` file (env vars take precedence):

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_CONFIG_DIR` | (required for OAuth; not needed for ADC) | Directory for the OAuth token and credentials |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | `$GOOGLE_CONFIG_DIR/service_account.json` | Service account key file |

Authentication tries OAuth first, then the service account, then Application
Default Credentials.

## CLI Commands

Run `gdrives show-drives` once to populate the drive-name cache
(`.gdrives/cache.json`); the `ls` and `download` commands resolve Drive paths
against it.

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

### Export Google Docs, Sheets, and Slides

```bash
gdrives export <doc-url> -o output.docx     # Google Doc -> .docx
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
gdrives sheets-update <sheet-url> "A1:C2" --values-file data.csv   # Overwrite a range
gdrives sheets-update <sheet-url> "A1" --values-file data.csv --raw  # Store literal strings
gdrives sheets-append <sheet-url> "Sheet1!A1" --values-file rows.csv  # Append after the table
gdrives sheets-clear <sheet-url> "Sheet1!A1:C10"        # Clear values (prompts first; -y skips)
```

Reads use the read-only scope (no re-consent for existing users). The write
commands (`sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`) request
the `spreadsheets` scope the first time and cache it in a separate token. Cells
are plain strings on both sides: `--values-file` reads a local CSV, and by
default `USER_ENTERED` parses formulas, dates, and numbers like the Sheets UI
(`--raw` stores the literal text). Use `--raw` when writing data from an
untrusted source, so a leading `=`, `+`, `-`, or `@` is stored verbatim rather
than evaluated as a formula.

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
`--raw` and the `USER_ENTERED` default apply as above.

### Download files and folders

```bash
gdrives download <file-url> -o ./out          # Single file -> ./out/<drive-name>
gdrives download "My Drive/refs/paper.pdf"    # Single file by path -> ./paper.pdf
gdrives download "My Drive/refs"              # Whole folder (recurses by default)
gdrives download "My Drive/refs" --depth 1    # Folder, flat (no recursion)
gdrives download "My Drive/refs" -y           # Skip the confirmation prompt
```

A source that resolves to a single file downloads immediately under its Drive
name. A folder is scanned first, showing a summary, then prompts before
downloading (`--depth` only affects folders). Google Docs, Sheets, and Slides
auto-export to `.docx` / `.xlsx` / `.pptx`; other Google-native types (Forms,
Drawings, etc.) are skipped.

### Show available drives

```bash
gdrives show-drives
```

Output includes URL, type (personal/shared), name, and ID for each accessible
drive, and is cached to `.gdrives/cache.json` for use by the other commands.

## Related projects

There are a few options out there, but most haven't been touched in years, and none did the mapping tasks implemented here.

- [PyDrive2](https://github.com/iterative/PyDrive2) — fork of PyDrive, high-level Google Drive wrapper
- [PyDrive](https://github.com/googlearchive/PyDrive) — the original high-level wrapper, now archived
- [gdrive](https://pypi.org/project/gdrive/) "Simple Google Drive wrapper to traverse files"
- [gdriver](https://pypi.org/project/gdriver/) "Actually usable Google Drive client"
- [drive](https://pypi.org/project/drive/) "Google Drive client"

## Security & privacy

- Read commands request **read-only** Drive access (`drive.readonly`) and never modify or delete anything in your Drive. Only the Sheets write commands (`sheets-update`, `sheets-append`, `sheets-clear`) request write access, via the `spreadsheets` scope; a read command never loads or requests it.
- The cached OAuth tokens (`$GOOGLE_CONFIG_DIR/gdrives_token.json` for read-only, `gdrives_token_rw.json` for the Sheets write scope) hold long-lived refresh tokens and are written with owner-only `0600` permissions. The write token is a separate file so requesting write access never clobbers or re-consents the read-only one. Keep `gdrives_credentials.json` and `service_account.json` out of version control and shared locations.
- `gdrives show-drives` writes `.gdrives/cache.json` with the names and IDs of every Drive you can access; it is gitignored by default — keep it out of shared locations.
