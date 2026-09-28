"""Shared Google Drive authentication and service builder."""

import functools
import json
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, redirect_stdout
from contextvars import ContextVar
from dataclasses import dataclass, field
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
# own token file (see _token_paths) so neither the read-only token nor the Sheets
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

# The scope sets `gdrives login --scope NAME` can grant, by name.
LOGIN_SCOPES = {
    "read": SCOPES,
    "sheets": SHEETS_WRITE_SCOPES,
    "docs": DOCS_WRITE_SCOPES,
    "drive": DRIVE_WRITE_SCOPES,
}

_SCOPE_PREFIX = "https://www.googleapis.com/auth/"

# The scopes a grant of each scope also serves, so a cached token with a broader
# grant is used for a narrower request instead of being re-consented. Each pair
# is confirmed against the per-method scope lists of the Drive v3, Sheets v4,
# and Docs v1 discovery documents: every method that accepts the narrower scope
# also accepts the broader one. `drive.file` serves no other scope, although
# the same lists accept it for Sheets and Docs methods, because it reaches only
# the files the app created or was handed.
_IMPLIES = {
    _SCOPE_PREFIX + broad: frozenset(_SCOPE_PREFIX + narrow for narrow in narrower)
    for broad, narrower in {
        "drive": (
            "drive.readonly",
            "drive.file",
            "drive.metadata",
            "drive.metadata.readonly",
            "spreadsheets",
            "spreadsheets.readonly",
            "documents",
            "documents.readonly",
        ),
        "drive.metadata": ("drive.metadata.readonly",),
        "spreadsheets": ("spreadsheets.readonly",),
        "documents": ("documents.readonly",),
    }.items()
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


class ConsentError(Exception):
    """A consent that was asked for with ``force`` could not run or finish."""


def _config_dir() -> Path | None:
    """Return the credentials directory, or None if GOOGLE_CONFIG_DIR is unset."""
    value = os.environ.get("GOOGLE_CONFIG_DIR")
    return Path(value) if value else None


def _derived_token_name(scopes: list[str]) -> str:
    """Return the token filename built from the scopes themselves.

    Each scope's last path segment, sorted so order never matters
    (``.../auth/documents`` -> ``gdrives_token_documents.json``).
    """
    parts = sorted(s.rstrip("/").rsplit("/", 1)[-1].replace(".", "-") for s in scopes)
    return "gdrives_token_" + "_".join(parts) + ".json"


def _token_name(scopes: list[str]) -> str:
    """Return the token filename for a scope set.

    Known sets map to their historical names; any other set gets the stable
    name derived from its scopes (see _derived_token_name).
    """
    key = frozenset(scopes)
    if key in _TOKEN_NAMES:
        return _TOKEN_NAMES[key]
    return _derived_token_name(sorted(key))


def _token_paths(scopes: list[str]) -> list[Path]:
    """Return every place a token for ``scopes`` can be, in lookup order.

    Every scope set gets its own token file, so requesting one kind of write
    access never clobbers — or forces a re-consent of — the shared read-only
    token or another write token. The historical name first, then the name
    derived from the scopes, which is where a consent writes when the
    historical file holds a grant it must not replace (see _consent_path). For
    most scope sets the two are one file.
    Empty when GOOGLE_CONFIG_DIR is unset.
    """
    config_dir = _config_dir()
    if config_dir is None:
        return []
    names = dict.fromkeys([_token_name(scopes), _derived_token_name(scopes)])
    return [config_dir / name for name in names]


def _recorded_scopes(token_path: Path) -> list[str] | None:
    """The granted scopes a token file records, or None when it records none.

    google-auth stores the granted scopes in the token JSON, as a list or a
    space-separated string. None for a file that is missing, cannot be parsed,
    or has no usable ``scopes`` entry.
    """
    try:
        granted = json.loads(token_path.read_text()).get("scopes")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(granted, str):
        granted = granted.split()
    if not isinstance(granted, list) or not all(isinstance(s, str) for s in granted):
        return None
    return granted


def _covers(granted: list[str], scopes: list[str]) -> bool:
    """True when a grant of ``granted`` serves every scope in ``scopes``.

    A scope serves itself and the scopes ``_IMPLIES`` lists for it, so a
    ``drive`` grant covers a ``spreadsheets`` request.
    """
    served = set(granted)
    for scope in granted:
        served |= _IMPLIES.get(scope, frozenset())
    return set(scopes) <= served


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


def _consent_path(scopes: list[str]) -> Path | None:
    """The file a new consent for ``scopes`` writes its token to, or None.

    The first of the token's places (see _token_paths) that is free, or that
    holds a grant the new one includes. A file holding any other grant, no
    recorded scopes, or content that does not parse may be a token its owner
    still needs (one a caller wrote itself, under a name gdrives later
    claimed), so it is never replaced. None when no place is left.
    """
    for path in _token_paths(scopes):
        if not path.exists():
            return path
        held = _recorded_scopes(path)
        if held is not None and _covers(scopes, held):
            return path
    return None


def _write_consent_token(scopes: list[str], creds: Any) -> None:
    """Persist the token of a new consent for ``scopes`` where it replaces nothing.

    Written to the historical name unless that file holds a grant the new one
    does not include; then to the derived name, with a warning naming both
    files. When that is taken as well the token is not persisted: the caller
    still holds valid in-memory credentials for this run.
    """
    expected, target = _token_paths(scopes)[0], _consent_path(scopes)
    if target is None:
        logger.warning(
            "OAuth token not saved: %s holds a grant the new consent does not "
            "include, and is left as it is",
            expected,
        )
        return
    if target != expected:
        logger.warning(
            "OAuth token written to %s: %s holds a grant the new consent does "
            "not include, and is left as it is",
            target,
            expected,
        )
    _write_token(target, creds)


def _load_token_reason(token_path: Path, scopes: list[str], *, warn: bool = True):
    """``(credentials, reason)`` for the cached OAuth token at ``token_path``.

    ``credentials`` is None, and ``reason`` one of PASSED_REASONS, when the
    token is not usable to try: ``"missing"`` (no file there), ``"scopes"``
    (its recorded grant does not cover ``scopes``, so it would 403 on the
    first call), or ``"unreadable"`` (it does not load — unparseable, or no
    usable ``scopes`` entry left it to the loader, which then failed).
    Otherwise ``reason`` is None: the token loaded, and it is the caller's to
    check for validity (see ``_needs_refresh`` and ``PASSED_REASONS``'s
    ``"invalid"``). Loading reads the file only; nothing is refreshed. ``warn``
    logs why a token present on disk was passed over.

    A grant that covers ``scopes`` only by implication (see _IMPLIES) is loaded
    with the scopes it records: a refresh that asks for a scope outside the
    grant can be refused.
    """
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        return None, "missing"
    granted = _recorded_scopes(token_path)
    if granted is not None:
        if not _covers(granted, scopes):
            if warn:
                logger.warning(
                    "cached OAuth token %s does not cover the requested scopes; "
                    "passing over it",
                    token_path,
                )
            return None, "scopes"
        if not set(scopes) <= set(granted):
            scopes = granted
    try:
        return Credentials.from_authorized_user_file(str(token_path), scopes), None
    except (OSError, ValueError, AttributeError, TypeError):
        if warn:
            logger.warning("could not load cached OAuth token %s", token_path)
        return None, "unreadable"


def _load_token(token_path: Path, scopes: list[str], *, warn: bool = True):
    """The cached OAuth credentials at ``token_path``, or None when none can be used.

    None when there is no token file, when its grant does not cover ``scopes``
    (it would 403 on the first call), or when it cannot be loaded. A token
    without a usable ``scopes`` entry is left to the loader. Loading reads the
    file only; nothing is refreshed. ``warn`` logs why a token present on disk
    was passed over.

    A grant that covers ``scopes`` only by implication (see _IMPLIES) is loaded
    with the scopes it records: a refresh that asks for a scope outside the
    grant can be refused.
    """
    creds, _reason = _load_token_reason(token_path, scopes, warn=warn)
    return creds


def _needs_refresh(creds: Any) -> bool:
    """True when cached credentials have expired but can be refreshed."""
    return bool(creds.expired and creds.refresh_token)


def _cached_tokens(scopes: list[str], *, warn: bool = True):
    """Yield ``(path, credentials)`` for each cached token usable for ``scopes``.

    In lookup order (see _token_paths). Nothing is refreshed here: a caller
    that finds a token's refresh refused moves on to the next one.
    """
    for token_path in _token_paths(scopes):
        creds = _load_token(token_path, scopes, warn=warn)
        if creds is not None:
            yield token_path, creds


def _cached_tokens_detailed(scopes: list[str], *, warn: bool = True):
    """Yield ``(path, credentials, reason)`` for every place a token can be.

    In lookup order (see _token_paths), unlike ``_cached_tokens``, which
    yields only the ones that loaded. ``reason`` is one of PASSED_REASONS
    when ``credentials`` is None, and None when the token loaded (its
    validity is still the caller's to check). Used by describe_credentials to
    report each cached token file it looked at and why, without a second
    derivation of the same decision.
    """
    for token_path in _token_paths(scopes):
        creds, reason = _load_token_reason(token_path, scopes, warn=warn)
        yield token_path, creds, reason


class _FlushedStdout:
    """stdout, flushed at every write, for the consent flow's prompt.

    With no terminal attached stdout is block-buffered, so the URL the flow
    prints would sit in the buffer for as long as the flow waits for a person
    to open it.
    """

    def __init__(self) -> None:
        self._stdout = sys.stdout

    def write(self, text: str) -> int:
        written = self._stdout.write(text)
        self._stdout.flush()
        return written

    def flush(self) -> None:
        self._stdout.flush()


def _can_consent(credentials_path: Path, *, force: bool = False) -> bool:
    """True when an OAuth consent can run: client secrets, and a TTY or ``force``."""
    return credentials_path.exists() and (force or _is_interactive())


def _no_consent(credentials_path: Path | None) -> ConsentError:
    """Why a consent asked for with ``force`` cannot run: OAuth is not configured."""
    if credentials_path is None:
        return ConsentError(
            "GOOGLE_CONFIG_DIR is not set, so there are no OAuth client "
            "secrets to consent with (docs/setup-oauth.md)"
        )
    return ConsentError(
        f"no OAuth client secrets at {credentials_path} (docs/setup-oauth.md)"
    )


def _timeout_error() -> type[Exception]:
    """The exception the consent flow raises when nobody answers in time.

    WSGITimeoutError, where google-auth-oauthlib has it. Releases up to 1.2.1
    raise the bare AttributeError it was later made from.
    """
    import google_auth_oauthlib.flow

    return getattr(google_auth_oauthlib.flow, "WSGITimeoutError", AttributeError)


def authenticate_oauth(
    scopes: list[str] | None = None,
    *,
    force: bool = False,
    timeout: int | None = None,
):
    """Authenticate with Google Drive via OAuth client secrets flow.

    Returns None when OAuth is not configured (GOOGLE_CONFIG_DIR unset, no client
    secrets file, or no interactive terminal to complete the browser flow), so
    authenticate() can fall through to other methods. ``scopes`` defaults to the
    read-only Drive scope; write commands pass a broader set.

    ``force`` is for a run with no terminal attached and a person still
    watching its output: when no cached token can be used, the consent runs
    whether or not stdin is a TTY, and OAuth that is not configured raises
    ConsentError instead of returning None. A cached token that can be used
    still is. ``timeout`` is how many seconds a consent waits for the person;
    when it runs out ConsentError is raised and no token file is touched.
    """
    scopes = scopes or SCOPES
    credentials_path = _credentials_path()
    if credentials_path is None:
        if force:
            raise _no_consent(credentials_path)
        return None

    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    for token_path, creds in _cached_tokens(scopes):
        if _needs_refresh(creds):
            try:
                creds.refresh(Request())
            except google.auth.exceptions.RefreshError:
                # The grant is revoked or expired: try the token's other
                # place, then consent again below. A network failure
                # (TransportError) propagates instead. A new consent can't
                # fix it and a service account can't reach Google either, so
                # the CLI reports it as is rather than opening a browser flow.
                continue
            _write_token(token_path, creds)
        if creds.valid:
            return creds
    # Fall through to other auth methods rather than blocking on a browser
    # flow when there's no client secrets file or no interactive terminal.
    if not _can_consent(credentials_path, force=force):
        if force:
            raise _no_consent(credentials_path)
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
    try:
        with redirect_stdout(_FlushedStdout()):
            creds = flow.run_local_server(
                port=0, open_browser=False, timeout_seconds=timeout
            )
    except _timeout_error() as e:
        if timeout is None:
            raise
        raise ConsentError(
            f"no consent within {timeout} seconds; no token was written"
        ) from e
    _write_consent_token(scopes, creds)
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


def authenticate(scopes: list[str] | None = None, *, force: bool = False):
    """Authenticate with Google Drive.

    Tries OAuth, then a service account key, then Application Default
    Credentials (e.g. `gcloud auth application-default login`). Raises a
    helpful SystemExit if none are configured. ``scopes`` defaults to read-only
    Drive access; pass a write scope (e.g. SHEETS_WRITE_SCOPES) for write ops.
    With ``force`` the credentials are OAuth or the call fails (see
    authenticate_oauth): nothing falls through to the other two.
    """
    creds = authenticate_oauth(scopes, force=force)
    creds = creds or authenticate_service_account(scopes)
    if creds:
        return creds
    try:
        return authenticate_adc(scopes)
    except google.auth.exceptions.GoogleAuthError:
        raise SystemExit(NO_CREDENTIALS_MESSAGE)


PASSED_REASONS = ("missing", "scopes", "unreadable", "invalid")


@dataclass(frozen=True)
class PassedToken:
    """A cached token file describe_credentials looked at and did not use.

    ``reason`` is one of PASSED_REASONS: ``"missing"`` (no file there),
    ``"scopes"`` (its recorded grant does not cover the scopes asked for),
    ``"unreadable"`` (the file does not load), or ``"invalid"`` (it loads and
    is neither valid nor refreshable).
    """

    path: Path
    reason: str


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

    The remaining fields say more, for a caller that wants to announce why:
    ``oauth_client`` is the OAuth client secrets file, when one is present
    (None means OAuth is not configured); ``terminal`` is whether a terminal
    is on stdin; ``consent_skipped`` is True when OAuth is configured, no
    cached token served, and no consent could run for lack of a terminal;
    ``service_account`` is the service account key file, when one exists,
    whichever credential was chosen; and ``passed_over`` lists each cached
    token file that was looked at and not used (see PassedToken), in the
    order ``_token_paths`` gives. Each has a default, so a CredentialInfo
    built as before this addition is unchanged, and these fields are left out
    of equality and hashing so a comparison built the old way still matches.
    """

    kind: str
    consent: bool = False
    refresh: bool = False
    identity: str | None = None
    source: Path | None = None
    oauth_client: Path | None = field(default=None, compare=False)
    terminal: bool = field(default=False, compare=False)
    consent_skipped: bool = field(default=False, compare=False)
    service_account: Path | None = field(default=None, compare=False)
    passed_over: tuple[PassedToken, ...] = field(default=(), compare=False)

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


def describe_credentials(
    scopes: list[str] | None = None, *, force: bool = False
) -> CredentialInfo:
    """Say which credential :func:`authenticate` will use for ``scopes``.

    Follows authenticate()'s precedence through the same helpers: a cached
    OAuth token that loads and is valid or refreshable, else an OAuth consent
    when client secrets and a terminal (or ``force``) allow one, else a
    service account key that exists, else Application Default Credentials.
    Reads local files only: no network call is made and no consent is
    started. Whether ADC is configured is left to google-auth at the first
    call, since finding out can take a network probe. With ``force`` nothing
    falls through either: ConsentError is raised where authenticate() raises it.

    Beyond ``kind``, ``consent``, ``refresh``, ``identity``, and ``source``,
    the returned info always carries the discovery fields described on
    CredentialInfo (``oauth_client``, ``terminal``, ``consent_skipped``,
    ``service_account``, ``passed_over``), whichever branch is taken.
    """
    scopes = scopes or SCOPES
    credentials_path = _credentials_path()
    oauth_client = (
        credentials_path
        if credentials_path is not None and credentials_path.exists()
        else None
    )
    terminal = _is_interactive()
    sa_path = _service_account_path()
    service_account = sa_path if sa_path is not None and sa_path.exists() else None
    passed_over: list[PassedToken] = []
    if credentials_path is not None:
        for token_path, creds, reason in _cached_tokens_detailed(scopes, warn=False):
            if creds is None:
                assert reason is not None
                passed_over.append(PassedToken(path=token_path, reason=reason))
                continue
            if _needs_refresh(creds):
                return CredentialInfo(
                    kind="oauth",
                    refresh=True,
                    source=token_path,
                    oauth_client=oauth_client,
                    terminal=terminal,
                    service_account=service_account,
                    passed_over=tuple(passed_over),
                )
            if creds.valid:
                return CredentialInfo(
                    kind="oauth",
                    source=token_path,
                    oauth_client=oauth_client,
                    terminal=terminal,
                    service_account=service_account,
                    passed_over=tuple(passed_over),
                )
            passed_over.append(PassedToken(path=token_path, reason="invalid"))
        if _can_consent(credentials_path, force=force):
            return CredentialInfo(
                kind="oauth",
                consent=True,
                source=credentials_path,
                oauth_client=oauth_client,
                terminal=terminal,
                service_account=service_account,
                passed_over=tuple(passed_over),
            )
    if force:
        raise _no_consent(credentials_path)
    consent_skipped = oauth_client is not None and not terminal
    if sa_path is not None and sa_path.exists():
        return CredentialInfo(
            kind="service_account",
            identity=_service_account_email(sa_path),
            source=sa_path,
            oauth_client=oauth_client,
            terminal=terminal,
            consent_skipped=consent_skipped,
            service_account=service_account,
            passed_over=tuple(passed_over),
        )
    return CredentialInfo(
        kind="adc",
        oauth_client=oauth_client,
        terminal=terminal,
        consent_skipped=consent_skipped,
        service_account=service_account,
        passed_over=tuple(passed_over),
    )


# The scope sets whose credential line this run has printed, or None when
# authentication announces nothing on its own (see announcing_credentials).
_announced: ContextVar[set[frozenset[str]] | None] = ContextVar(
    "gdrives_announced_credentials", default=None
)


@contextmanager
def announcing_credentials() -> Generator[None, None, None]:
    """Announce, within the block, each authentication that is about to wait.

    Every service built inside prints its credential line first when a consent
    or a token refresh is coming (see announce_credentials), once per scope
    set. The CLI runs every command inside one; a library caller's stderr is
    left alone unless it enters one too.
    """
    token = _announced.set(set())
    try:
        yield
    finally:
        _announced.reset(token)


def announce_credentials(
    scopes: list[str] | None = None, *, always: bool = False, force: bool = False
) -> None:
    """Say on stderr which credential requests with ``scopes`` will use.

    Printed when describe_credentials() reports a consent or a refresh, so a
    run waiting on a browser consent does not look hung; with ``always``
    whatever it reports, so a write is not made as an unexpected identity.
    Inside announcing_credentials() a scope set's line is printed once.
    """
    key = frozenset(scopes or SCOPES)
    seen = _announced.get()
    if seen is not None and key in seen:
        return
    info = describe_credentials(scopes, force=force)
    if always or info.consent or info.refresh:
        print(f"Credential: {info}", file=sys.stderr)
        if seen is not None:
            seen.add(key)


@functools.cache
def _credentials(scopes: frozenset[str], force: bool = False):
    """Authenticate once per scope set for the life of the process.

    A command that builds two clients with the same scopes, such as the Drive
    client that resolves a path and the Sheets or Docs client that reads the
    file, then authenticates once instead of twice. With a service account or
    ADC, each authentication costs a token round-trip.
    """
    if _announced.get() is not None:
        announce_credentials(sorted(scopes), force=force)
    return authenticate(sorted(scopes), force=force)


def _build_service(
    api: str, version: str, scopes: list[str] | None, *, force: bool = False
):
    """Build the ``api``/``version`` client with credentials for ``scopes``."""
    from googleapiclient.discovery import build

    creds = _credentials(frozenset(scopes or SCOPES), force)
    return build(api, version, credentials=creds)


def build_drive_service(scopes: list[str] | None = None, *, force: bool = False):
    """Authenticate and return a Drive v3 service.

    ``force``, here and in the other builders, is authenticate_oauth()'s: a
    consent that is needed runs even with no terminal attached.
    """
    return _build_service("drive", "v3", scopes, force=force)


def build_sheets_service(scopes: list[str] | None = None, *, force: bool = False):
    """Authenticate and return a Sheets v4 service.

    Defaults to the read-only Drive scope (enough for ``spreadsheets.values.get``
    and reusing the shared read-only token). Pass SHEETS_WRITE_SCOPES for the
    update/append/clear operations, which persist a separate write token.
    """
    return _build_service("sheets", "v4", scopes, force=force)


def build_docs_service(scopes: list[str] | None = None, *, force: bool = False):
    """Authenticate and return a Docs v1 service.

    Defaults to the read-only Drive scope (enough for ``documents.get`` and
    reusing the shared read-only token). Pass DOCS_WRITE_SCOPES for
    ``batchUpdate``/``create``, which persist a separate write token.
    """
    return _build_service("docs", "v1", scopes, force=force)
