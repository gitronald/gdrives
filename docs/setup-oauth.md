# OAuth Setup for Google Drive API

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Drive API** (APIs & Services > Library > search "Google Drive API")
4. To use the `sheets-*` commands, also enable the **Google Sheets API** (same Library page)
5. To use the `docs-*` commands, also enable the **Google Docs API** (same Library page)

## 2. Set up OAuth consent screen

1. Go to **APIs & Services** > **OAuth consent screen**
2. Choose **External** user type
3. Fill in the required fields (app name, support email)
4. Leave the app in **Testing** mode
5. Under **Test users** (or **Audience** in newer console versions), add your Google email address

## 3. Create OAuth credentials

1. Go to **Credentials** > **Create Credentials** > **OAuth client ID**
2. Application type: **Desktop app**
3. Download the JSON file
4. Save it as `$GOOGLE_CONFIG_DIR/gdrives_credentials.json`

## 4. Authorize

Run any `gdrives` command (e.g., `uv run gdrives show-drives`). A browser window will open for one-time OAuth authorization. After approving, a token is cached to `$GOOGLE_CONFIG_DIR/gdrives_token.json` for future runs.

If the token expires or is revoked, the auth flow will automatically re-trigger.

A consent needs a terminal: when stdin is not one, a command skips OAuth and falls through to a service account or ADC. To consent anyway, for example from a tool that captures stdin while you watch its output, run `gdrives login`:

```bash
gdrives login                  # read access, for every read command
gdrives login --scope sheets   # the sheets-* write commands
gdrives login --scope docs     # the docs-* write commands
gdrives login --scope drive    # mv, upload, sheets-create
gdrives login --timeout 60     # wait 60 seconds for the consent (default 300)
```

It prints the consent URL, waits for the browser to come back, caches the token, and prints the credential the commands will now use. When a cached token already serves the scope, nothing is asked. When the time runs out it exits 1 and no token file is touched. It also exits 1 when the token could not be saved, because a file in its place holds something a consent must not replace: move that file and run it again. It is also the way to grant again after a token's refresh has failed.

Before any command waits on a consent or a token refresh, it says so on stderr with a line starting `Credential:`. The same line is printed when a consent was needed but there is no terminal for one, and the command goes on as the service account or Application Default Credentials; it then ends with `(OAuth is configured, but no cached token serves these scopes and there is no terminal for a consent; run gdrives login)`. Run `gdrives login` in a terminal to cache a token. Nothing is printed when OAuth is not configured.

The first `sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`, `sheets-add-rule`, or `sheets-delete-rule` run opens a second authorization for the `spreadsheets` write scope, cached separately in `$GOOGLE_CONFIG_DIR/gdrives_token_rw.json`. Likewise, the first `docs-update`, `docs-append`, `docs-replace`, `docs-clear`, or `docs-create` run authorizes the `documents` write scope, cached in `$GOOGLE_CONFIG_DIR/gdrives_token_documents.json`. The first `mv`, `upload`, or `sheets-create` run without `--dry-run` authorizes the full `drive` scope, cached in `$GOOGLE_CONFIG_DIR/gdrives_token_drive.json`. The scope is requested up front, so this happens even when a move turns out to be a no-op; with `--dry-run` each of them stays on the read-only token. Read commands keep using the read-only token untouched, and each write scope has its own token, so authorizing one never re-prompts for another. A cached token whose grant does not cover the requested scope is passed over and re-authorized instead of failing with a 403.

A cached token with a broader grant than a command asks for is used as it is: a token granted `drive` serves the read commands and the Sheets and Docs write commands too, with no new consent.

## Files

| File | Description |
|---|---|
| `gdrives_credentials.json` | OAuth client secret (downloaded from Cloud Console) |
| `gdrives_token.json` | Read-only token, auto-generated after first authorization |
| `gdrives_token_rw.json` | Sheets write token, auto-generated on first Sheets write command |
| `gdrives_token_documents.json` | Docs write token, auto-generated on first Docs write command |
| `gdrives_token_drive.json` | Drive write token, auto-generated on first `mv`, `upload`, or `sheets-create` that writes |

The names `gdrives_token*.json` are reserved for the tokens `gdrives` writes. If your own code keeps a token in the same directory, give it another name. A consent never overwrites a token file that holds a grant the new one does not include (or no recorded scopes, or content that does not parse): the new token is written under the name derived from its scopes instead, a warning names both files, and later runs look in both places.

| Scope | Token file | Written instead when that file holds another grant |
|---|---|---|
| `drive.readonly` | `gdrives_token.json` | `gdrives_token_drive-readonly.json` |
| `spreadsheets` | `gdrives_token_rw.json` | `gdrives_token_spreadsheets.json` |
| `documents` | `gdrives_token_documents.json` | (none: the token is used for the run and not saved) |
| `drive` | `gdrives_token_drive.json` | (none: the token is used for the run and not saved) |

Scopes: `drive.readonly` (read commands), `spreadsheets` (Sheets write commands: `sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`, `sheets-add-rule`, `sheets-delete-rule`), `documents` (Docs write commands: `docs-update`, `docs-append`, `docs-replace`, `docs-clear`, `docs-create`), and `drive` (`mv`, `upload`, `sheets-create`).
