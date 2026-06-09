# OAuth Setup for Google Drive API

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Drive API** (APIs & Services > Library > search "Google Drive API")

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

## Files

| File | Description |
|---|---|
| `gdrives_credentials.json` | OAuth client secret (downloaded from Cloud Console) |
| `gdrives_token.json` | Auto-generated after first authorization |

Scope: `drive.readonly` (read-only access to Google Drive).
