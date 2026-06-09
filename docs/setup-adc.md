# gcloud / ADC Setup for Google Drive API

The simplest option if you already use the [gcloud CLI](https://cloud.google.com/sdk/docs/install). It uses Application Default Credentials (ADC) — no client-secrets file and no token file to manage. `gdrives` falls back to ADC automatically when no OAuth or service-account credentials are configured, so no `GOOGLE_CONFIG_DIR` is required.

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Drive API** (APIs & Services > Library > search "Google Drive API")

## 2. Log in with the Drive scope

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform
```

A browser opens for consent. This writes credentials to `~/.config/gcloud/application_default_credentials.json`, which `gdrives` reads automatically.

> **The `--scopes` flag is required.** A plain `gcloud auth application-default login` grants only the `cloud-platform` scope, which does **not** include Drive — Drive calls would fail with an insufficient-scope (403) error. Include `drive.readonly` as shown. (`cloud-platform` is added so the quota-project step below can verify the project.)

## 3. Set a quota project

User ADC needs a project to attribute API usage to:

```bash
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Without this you'll see a warning like "authenticated ... without a quota project," and some calls may be rejected. Use the project where you enabled the Drive API.

## 4. Run

```bash
uv run gdrives show-drives
```

No `GOOGLE_CONFIG_DIR`, no credential files. The same login is reused by `ls`, `export`, and `download`.

## Notes

- ADC is the last method `gdrives` tries (after OAuth and service account), so it's only used when neither of those is configured.
- Other ADC sources work too: set `GOOGLE_APPLICATION_CREDENTIALS` to a service-account key, or run on GCE/Cloud Run, where ADC is provided by the metadata server.
- To switch identities, re-run the login command; to remove cached ADC, run `gcloud auth application-default revoke`.

Scope: `drive.readonly` (read-only access to Google Drive).
