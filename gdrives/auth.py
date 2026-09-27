"""Shared Google Drive authentication and service builder."""

import functools
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import google.auth.exceptions
from dotenv import find_dotenv, load_dotenv

from gdrives.local import PRIVATE, atomic_output


def _load_env() -> None:
    """Load the ``.env`` in the working directory or its nearest ancestor.

    A bare ``load_dotenv()`` searches upward from this module's own directory,
    which for an installed tool is site-packages, not the project a command
    runs in: it would miss the project's ``.env`` and could load an unrelated
    one such as ``~/.env``. Starting from the working directory matches
    ``.gdrives/cache.json``, which is relative to it too. Variables already
    set in the environment win.
    """
    load_dotenv(find_dotenv(usecwd=True))


_load_env()

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Write scope for the Sheets API (spreadsheets.values.update/append/clear). Kept
# separate from the read-only default so read commands never request write
# access; write commands opt in explicitly (see build_sheets_service).
SHEETS_WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Write scope for the Docs API (documents.batchUpdate/create). Same opt-in split
# as Sheets: only the docs-* write commands request it, and it is cached in its
# own token file (see _token_path) so neither the read-only token nor the Sheets
# write token is touched.
DOCS_WRITE_SCOPES = ["https://www.googleapis.com/auth/documents"]

# Write scope for the Drive API itself (files.update, used by `mv` to rename and
# reparent). Same opt-in split again: the read-only default cannot call
# files.update, so only `mv` requests this, and it lands in its own
# gdrives_token_drive.json rather than replacing the read-only token.
DRIVE_WRITE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Token filename per scope set. The read-only default and the Sheets write scope
# keep their historical names so existing tokens stay valid; any other set gets
# a name derived from its scopes (see _token_name).
_TOKEN_NAMES = {
    frozenset(SCOPES): "gdrives_token.json",
    frozenset(SHEETS_WRITE_SCOPES): "gdrives_token_rw.json",
}

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


def _token_name(scopes: list[str]) -> str:
    """Return the token filename for a scope set.

    Known sets map to their historical names; any other set gets a stable name
    built from each scope's last path segment, sorted so order never matters
    (``.../auth/documents`` -> ``gdrives_token_documents.json``).
    """
    key = frozenset(scopes)
    if key in _TOKEN_NAMES:
        return _TOKEN_NAMES[key]
    parts = sorted(s.rstrip("/").rsplit("/", 1)[-1].replace(".", "-") for s in key)
    return "gdrives_token_" + "_".join(parts) + ".json"


def _token_path(scopes: list[str] | None = None) -> Path | None:
    """Return the OAuth token path for the given scopes, or None if unconfigured.

    Every scope set gets its own token file, so requesting one kind of write
    access never clobbers — or forces a re-consent of — the shared read-only
    token or another write token. The read-only default keeps
    ``gdrives_token.json`` and the Sheets write scope ``gdrives_token_rw.json``.
    """
    config_dir = _config_dir()
    if config_dir is None:
        return None
    return config_dir / _token_name(scopes or SCOPES)


def _token_covers(token_path: Path, scopes: list[str]) -> bool:
    """True unless the cached token records granted scopes that miss ``scopes``.

    google-auth stores the granted scopes in the token JSON. A token whose grant
    does not cover the request would load fine and then 403 on the first call,
    so the caller discards it and re-consents instead. A token without a
    ``scopes`` entry, or one that cannot be parsed, is left to the normal loader.
    """
    try:
        granted = json.loads(token_path.read_text()).get("scopes")
    except (OSError, ValueError, AttributeError):
        return True
    if isinstance(granted, str):
        granted = granted.split()
    if not isinstance(granted, list) or not all(isinstance(s, str) for s in granted):
        return True
    return set(scopes) <= set(granted)


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
        with atomic_output(token_path, mode=PRIVATE) as f:
            f.write(creds.to_json().encode("utf-8"))
    except OSError:
        logger.warning("could not persist OAuth token to %s", token_path)


def _load_token(token_path: Path, scopes: list[str], *, warn: bool = True):
    """The cached OAuth credentials at ``token_path``, or None when none can be used.

    None when there is no token file, when its grant does not cover ``scopes``
    (it would 403 on the first call), or when it cannot be loaded. Loading reads
    the file only; nothing is refreshed. ``warn`` logs why a token present on
    disk was passed over.
    """
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        return None
    if not _token_covers(token_path, scopes):
        if warn:
            logger.warning(
                "cached OAuth token %s does not cover the requested scopes; "
                "re-authorizing",
                token_path,
            )
        return None
    try:
        return Credentials.from_authorized_user_file(str(token_path), scopes)
    except (OSError, ValueError, AttributeError, TypeError):
        if warn:
            logger.warning("could not load cached OAuth token %s", token_path)
        return None


def _needs_refresh(creds: Any) -> bool:
    """True when cached credentials have expired but can be refreshed."""
    return bool(creds.expired and creds.refresh_token)


def _can_consent(credentials_path: Path) -> bool:
    """True when an interactive OAuth consent can run: client secrets and a TTY."""
    return credentials_path.exists() and _is_interactive()


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
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = _load_token(token_path, scopes)
    if creds and _needs_refresh(creds):
        try:
            creds.refresh(Request())
        except google.auth.exceptions.RefreshError:
            # The grant is revoked or expired: consent again below. A network
            # failure (TransportError) propagates instead. A new consent can't
            # fix it and a service account can't reach Google either, so the
            # CLI reports it as is rather than opening a browser flow.
            creds = None
        else:
            _write_token(token_path, creds)
    if not creds or not creds.valid:
        # Fall through to other auth methods rather than blocking on a browser
        # flow when there's no client secrets file or no interactive terminal.
        if not _can_consent(credentials_path):
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


@dataclass(frozen=True)
class CredentialInfo:
    """Which credential a call will authenticate with, as far as is known locally.

    ``kind`` is ``"oauth"``, ``"service_account"``, or ``"adc"``. ``consent``
    is True when an interactive browser consent runs first. ``refresh`` is True
    when a cached OAuth token has expired and is refreshed first (if the grant
    was revoked, that refresh fails and a consent runs instead). ``identity``
    is the account, when the credential names it without a network call (a
    service account's ``client_email``). ``source`` is the file the
    credential comes from. No token, key, or secret is ever held here.
    """

    kind: str
    consent: bool = False
    refresh: bool = False
    identity: str | None = None
    source: Path | None = None

    def __str__(self) -> str:
        if self.kind == "oauth":
            if self.consent:
                text = f"OAuth, after an interactive consent (client {self.source})"
            else:
                text = f"OAuth token {self.source}"
                if self.refresh:
                    text += ", refreshed first"
        elif self.kind == "service_account":
            who = self.identity or "(client_email unreadable)"
            text = f"service account {who} (key {self.source})"
        else:
            text = "Application Default Credentials"
        return text


def _service_account_email(path: Path) -> str | None:
    """The ``client_email`` of a service account key file, or None if unreadable."""
    try:
        email = json.loads(path.read_text()).get("client_email")
    except (OSError, ValueError, AttributeError):
        return None
    return email if isinstance(email, str) and email else None


def describe_credentials(scopes: list[str] | None = None) -> CredentialInfo:
    """Say which credential :func:`authenticate` will use for ``scopes``.

    Follows authenticate()'s precedence through the same helpers: a cached
    OAuth token that loads and is valid or refreshable, else an OAuth consent
    when client secrets and a terminal allow one, else a service account key
    that exists, else Application Default Credentials. Reads local files only:
    no network call is made and no consent is started. Whether ADC is
    configured is left to google-auth at the first call, since finding out
    can take a network probe.
    """
    scopes = scopes or SCOPES
    token_path = _token_path(scopes)
    credentials_path = _credentials_path()
    if token_path is not None and credentials_path is not None:
        creds = _load_token(token_path, scopes, warn=False)
        if creds and _needs_refresh(creds):
            return CredentialInfo(kind="oauth", refresh=True, source=token_path)
        if creds and creds.valid:
            return CredentialInfo(kind="oauth", source=token_path)
        if _can_consent(credentials_path):
            return CredentialInfo(kind="oauth", consent=True, source=credentials_path)
    sa_path = _service_account_path()
    if sa_path is not None and sa_path.exists():
        return CredentialInfo(
            kind="service_account",
            identity=_service_account_email(sa_path),
            source=sa_path,
        )
    return CredentialInfo(kind="adc")


@functools.cache
def _credentials(scopes: frozenset[str]):
    """Authenticate once per scope set for the life of the process.

    A command that builds two clients with the same scopes, such as the Drive
    client that resolves a path and the Sheets or Docs client that reads the
    file, then authenticates once instead of twice. With a service account or
    ADC, each authentication costs a token round-trip.
    """
    return authenticate(sorted(scopes))


def _build_service(api: str, version: str, scopes: list[str] | None):
    """Build the ``api``/``version`` client with credentials for ``scopes``."""
    from googleapiclient.discovery import build

    creds = _credentials(frozenset(scopes or SCOPES))
    return build(api, version, credentials=creds)


def build_drive_service(scopes: list[str] | None = None):
    """Authenticate and return a Drive v3 service."""
    return _build_service("drive", "v3", scopes)


def build_sheets_service(scopes: list[str] | None = None):
    """Authenticate and return a Sheets v4 service.

    Defaults to the read-only Drive scope (enough for ``spreadsheets.values.get``
    and reusing the shared read-only token). Pass SHEETS_WRITE_SCOPES for the
    update/append/clear operations, which persist a separate write token.
    """
    return _build_service("sheets", "v4", scopes)


def build_docs_service(scopes: list[str] | None = None):
    """Authenticate and return a Docs v1 service.

    Defaults to the read-only Drive scope (enough for ``documents.get`` and
    reusing the shared read-only token). Pass DOCS_WRITE_SCOPES for
    ``batchUpdate``/``create``, which persist a separate write token.
    """
    return _build_service("docs", "v1", scopes)
