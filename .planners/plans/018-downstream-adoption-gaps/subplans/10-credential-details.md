# Say more in describe_credentials

Step 10 of [plan 018](../plan.md). Added to the plan on 2026-09-27, from a second
list of gaps by the same downstream caller.

## Spec

### Goal

`CredentialInfo` says enough for a caller to announce which credential a run uses and
why, with no private helper. A downstream caller that announces a fall back to the
service account calls three private functions of `gdrives.auth`: `_credentials_path`,
`_service_account_path`, and `_is_interactive`. `CredentialInfo` cannot tell "OAuth
is not configured" from "OAuth is configured, and was skipped for lack of a
terminal", and it cannot say why a cached token was passed over.

### Design

New fields of `CredentialInfo`, each with a default, so a `CredentialInfo` built by a
caller or a test as today is unchanged:

- `oauth_client: Path | None`: the OAuth client secrets file, when one is present.
  None means OAuth is not configured.
- `terminal: bool`: whether a terminal is on stdin, which a consent needs unless
  forced.
- `consent_skipped: bool`: True when OAuth is configured, no cached token served, and
  no consent could run for lack of a terminal. This is the case a caller announces.
- `service_account: Path | None`: the service account key file, when one exists,
  whichever credential was chosen.
- `passed_over: tuple[PassedToken, ...]`: each cached token file that was looked at
  and not used, in the order `_token_paths` gives.

`PassedToken`, a frozen dataclass: `path`, and `reason`, one of `PASSED_REASONS`:

- `missing`: no file at that path
- `scopes`: the file's recorded grant does not cover the scopes asked for
- `unreadable`: the file does not load (unparseable, or no recorded scopes)
- `invalid`: the token loads and is neither valid nor refreshable

The reasons come from the code that decides them. `_load_token` and `_cached_tokens`
already tell these cases apart in order to warn or skip. They are changed to return
the reason, and `describe_credentials` and `authenticate_oauth` both read it from
there. The reasons are not worked out a second time beside them.

### Rules that stay

- **No network call.** `describe_credentials` reads local files only.
- **No token, key, or secret is held.** The new fields are paths, booleans, and
  reason names. A test asserts that no field of a `CredentialInfo` built over real
  token and key fixtures holds any of their secret values.
- **The credential line is unchanged.** `CredentialInfo.__str__` prints what it
  prints today, since commands print it and callers may match it.
- `describe_credentials(force=True)` raises `ConsentError` where it does today.

### The private helpers

The fields make the three helpers unnecessary for the announcement: `oauth_client`
for `_credentials_path`, `service_account` for `_service_account_path`, and
`terminal` for `_is_interactive`. No public name is added for them. The private
names stay as they are.

### Tests

- Each field, over each branch of `describe_credentials`: a cached token, a
  refresh, a consent, a service account, and ADC.
- OAuth not configured, against OAuth configured and skipped with no terminal.
- Each reason of `PassedToken`, and the order of several.
- A token passed over for its scopes beside one that serves.
- The secrets test above.
- The string form of each kind is what it was.
- No test reaches the network: the fake credentials of `tests/test_auth.py` are
  used throughout.

### Docs

- README: the section on credentials names the new fields, with an example that
  announces a fall back.
- `docs/sheets-sync.md` where it shows `describe_credentials`, if it does.
- CHANGELOG `[Unreleased]` / Added.

### Out of scope

- Any change to which credential is chosen, or to the consent.
- Saying whether ADC is configured, which can take a network probe.
