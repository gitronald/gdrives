# Service Account Setup for Google Drive API

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use an existing one)
3. Enable the **Google Drive API** (APIs & Services > Library > search "Google Drive API")

## 2. Create the service account

1. Go to **IAM & Admin > Service Accounts**
2. Click **Create Service Account**
3. Fill in:
   - **Service account name** — something descriptive like `drive-reader`
   - **Service account ID** — auto-fills based on the name, this becomes the email
   - **Description** — optional, e.g., "Read access to shared drive X"
4. Click **Create and Continue**
5. Skip the optional grant steps (they control GCP resources, not Drive) — click **Done**

## 3. Create and download the key

1. Click on the service account you just created
2. Go to the **Keys** tab
3. Click **Add Key > Create new key**
4. Select **JSON** format, click **Create**
5. Save the downloaded file as `$GOOGLE_CONFIG_DIR/service_account.json` (or set `GOOGLE_SERVICE_ACCOUNT_PATH` to a custom location)

The JSON file contains the private key, client email, and project ID. Treat it like a password — don't commit it to git.

## 4. Share Drive content with the service account

Copy the service account email (e.g., `drive-reader@my-project.iam.gserviceaccount.com`) from the **Details** tab, then share Drive folders or shared drives with it:

- **Shared drive** — right-click the drive > Manage members > paste the SA email > choose a role
- **Individual folder** — right-click > Share > paste the SA email

Available roles:

| Role | Access |
|---|---|
| Viewer | Read-only |
| Commenter | Read + comment |
| Contributor | Read + write files |
| Content manager | Read + write + organize |
| Manager | Full control including membership |

The service account can only see what's explicitly shared with it.

## Managing keys

```bash
# List existing keys
gcloud iam service-accounts keys list \
  --iam-account=drive-reader@my-project.iam.gserviceaccount.com

# Create a new key
gcloud iam service-accounts keys create new-key.json \
  --iam-account=drive-reader@my-project.iam.gserviceaccount.com

# Delete a key (revokes access for anyone using it)
gcloud iam service-accounts keys delete KEY_ID \
  --iam-account=drive-reader@my-project.iam.gserviceaccount.com
```

Each service account can have up to 10 keys. To rotate: create a new key, distribute it, then delete the old one.

## Controlling access

There are no "scopes" baked into the service account or its key file. Access is controlled at two levels:

1. **What you share** — the SA can only see Drive folders and files explicitly shared with its email. Unshare to revoke.
2. **What the code requests** — the `scopes` parameter at authentication time determines allowed operations (e.g., `drive.readonly` vs `drive`). Narrower scopes are always safer.

To revoke access completely — delete all keys, or delete the service account:

```bash
gcloud iam service-accounts delete \
  drive-reader@my-project.iam.gserviceaccount.com
```
