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

The first `sheets-update`, `sheets-append`, `sheets-clear`, or `sheets-set` run opens a second authorization for the `spreadsheets` write scope, cached separately in `$GOOGLE_CONFIG_DIR/gdrives_token_rw.json`. Likewise, the first `docs-update`, `docs-append`, `docs-replace`, `docs-clear`, or `docs-create` run authorizes the `documents` write scope, cached in `$GOOGLE_CONFIG_DIR/gdrives_token_documents.json`. The first `mv` run without `--dry-run` authorizes the full `drive` scope, cached in `$GOOGLE_CONFIG_DIR/gdrives_token_drive.json`. The scope is requested up front, so this happens even when the move turns out to be a no-op; only `mv --dry-run` stays on the read-only token. Read commands keep using the read-only token untouched, and each write scope has its own token, so authorizing one never re-prompts for another. A cached token whose grant does not cover the requested scope is discarded and re-authorized instead of failing with a 403.

## Files

| File | Description |
|---|---|
| `gdrives_credentials.json` | OAuth client secret (downloaded from Cloud Console) |
| `gdrives_token.json` | Read-only token, auto-generated after first authorization |
| `gdrives_token_rw.json` | Sheets write token, auto-generated on first Sheets write command |
| `gdrives_token_documents.json` | Docs write token, auto-generated on first Docs write command |
| `gdrives_token_drive.json` | Drive write token, auto-generated on first `mv` that writes |

Scopes: `drive.readonly` (read commands), `spreadsheets` (Sheets write commands: `sheets-update`, `sheets-append`, `sheets-clear`, `sheets-set`), `documents` (Docs write commands: `docs-update`, `docs-append`, `docs-replace`, `docs-clear`, `docs-create`), and `drive` (`mv`).
