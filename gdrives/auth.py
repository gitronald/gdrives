"""Shared Google Drive authentication and service builder."""

import logging
import os
import sys
from pathlib import Path
from typing import Any

import google.auth.exceptions
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Write scope for the Sheets API (spreadsheets.values.update/append/clear). Kept
# separate from the read-only default so read commands never request write
# access; write commands opt in explicitly (see build_sheets_service).
SHEETS_WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

NO_CREDENTIALS_MESSAGE = (
    "Error: no Google Drive credentials found. Set up one of:\n"
    "  - OAuth: put gdrives_credentials.json in $GOOGLE_CONFIG_DIR "
    "(docs/setup-oauth.md)\n"
    "  - Service account: set GOOGLE_SERVICE_ACCOUNT_PATH, or put "
    "service_account.json in $GOOGLE_CONFIG_DIR "
    "(docs/setup-service-account.md)\n"
    "  - gcloud/ADC: run 'gcloud auth application-default login "
    "--scopes=https://www.googleapis.com/auth/drive.readonly' "
    "(docs/setup-adc.md)"
)


def _config_dir() -> Path | None:
    """Return the credentials directory, or None if GOOGLE_CONFIG_DIR is unset."""
    value = os.environ.get("GOOGLE_CONFIG_DIR")
    return Path(value) if value else None


def _token_path(scopes: list[str] | None = None) -> Path | None:
    """Return the OAuth token path for the given scopes, or None if unconfigured.

    Write scopes get a distinct token file (``gdrives_token_rw.json``) so
    requesting write access never clobbers — or forces a re-consent of — the
    shared read-only token. The read-only default keeps ``gdrives_token.json``.
    """
    config_dir = _config_dir()
    if config_dir is None:
        return None
    name = "gdrives_token.json" if scopes in (None, SCOPES) else "gdrives_token_rw.json"
    return config_dir / name


def _credentials_path() -> Path | None:
    config_dir = _config_dir()
    return config_dir / "gdrives_credentials.json" if config_dir else None


def _service_account_path() -> Path | None:
    value = os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH")
    if value:
        return Path(value)
    config_dir = _config_dir()
    return config_dir / "service_account.json" if config_dir else None


def _is_interactive() -> bool:
    """True when stdin is a TTY, i.e. an interactive OAuth browser flow can run."""
    return sys.stdin.isatty()


def _write_token(token_path: Path, creds: Any) -> None:
    """Persist OAuth credentials atomically with owner-only (0600) permissions.

    The token file holds a long-lived refresh token, so it must not be group- or
    world-readable. Writing through a 0600 temp file and an atomic rename avoids
    both a loose-permission window and a partially written token on failure. A
    failed write is logged, not raised — the caller still holds valid in-memory
    credentials for this run.
    """
    try:
        tmp = token_path.with_name(token_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())
        tmp.replace(token_path)
    except OSError:
        logger.warning("could not persist OAuth token to %s", token_path)


def authenticate_oauth(scopes: list[str] | None = None):
    """Authenticate with Google Drive via OAuth client secrets flow.

    Returns None when OAuth is not configured (GOOGLE_CONFIG_DIR unset, no client
    secrets file, or no interactive terminal to complete the browser flow), so
    authenticate() can fall through to other methods. ``scopes`` defaults to the
    read-only Drive scope; write commands pass a broader set.
    """
    scopes = scopes or SCOPES
    token_path = _token_path(scopes)
    credentials_path = _credentials_path()
    if token_path is None or credentials_path is None:
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except google.auth.exceptions.RefreshError:
            creds = None
        else:
            _write_token(token_path, creds)
    if not creds or not creds.valid:
        # Fall through to other auth methods rather than blocking on a browser
        # flow when there's no client secrets file or no interactive terminal.
        if not credentials_path.exists() or not _is_interactive():
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
        creds = flow.run_local_server(port=0, open_browser=False)
        _write_token(token_path, creds)
    return creds


def authenticate_service_account(scopes: list[str] | None = None):
    """Authenticate with Google Drive via a service account key file.

    Returns None when no key file is configured or present.
    """
    from google.oauth2.service_account import Credentials

    sa_path = _service_account_path()
    if sa_path is None or not sa_path.exists():
        return None
    return Credentials.from_service_account_file(str(sa_path), scopes=scopes or SCOPES)


def authenticate_adc(scopes: list[str] | None = None):
    """Authenticate with Google Drive via Application Default Credentials."""
    import google.auth

    creds, _ = google.auth.default(scopes=scopes or SCOPES)
    return creds


def authenticate(scopes: list[str] | None = None):
    """Authenticate with Google Drive.

    Tries OAuth, then a service account key, then Application Default
    Credentials (e.g. `gcloud auth application-default login`). Raises a
    helpful SystemExit if none are configured. ``scopes`` defaults to read-only
    Drive access; pass a write scope (e.g. SHEETS_WRITE_SCOPES) for write ops.
    """
    creds = authenticate_oauth(scopes) or authenticate_service_account(scopes)
    if creds:
        return creds
    try:
        return authenticate_adc(scopes)
    except google.auth.exceptions.GoogleAuthError:
        raise SystemExit(NO_CREDENTIALS_MESSAGE)


def build_drive_service(scopes: list[str] | None = None):
    """Authenticate and return a Drive v3 service."""
    from googleapiclient.discovery import build

    creds = authenticate(scopes)
    return build("drive", "v3", credentials=creds)


def build_sheets_service(scopes: list[str] | None = None):
    """Authenticate and return a Sheets v4 service.

    Defaults to the read-only Drive scope (enough for ``spreadsheets.values.get``
    and reusing the shared read-only token). Pass SHEETS_WRITE_SCOPES for the
    update/append/clear operations, which persist a separate write token.
    """
    from googleapiclient.discovery import build

    creds = authenticate(scopes)
    return build("sheets", "v4", credentials=creds)
