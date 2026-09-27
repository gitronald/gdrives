"""Tests for gdrives.auth — credential discovery and the fallback chain."""

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import google.auth.exceptions
import pytest
from helpers import plant_scratch_symlink

from gdrives import auth

# -- _config_dir and path helpers --


class TestConfigDir:
    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._config_dir() is None

    def test_path_when_set(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._config_dir() == Path("/tmp/cfg")


class TestTokenAndCredentialsPaths:
    def test_none_when_config_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._token_path() is None
        assert auth._credentials_path() is None

    def test_paths_under_config_dir(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_path() == Path("/tmp/cfg/gdrives_token.json")
        assert auth._credentials_path() == Path("/tmp/cfg/gdrives_credentials.json")


class TestServiceAccountPath:
    def test_env_override_used_without_config_dir(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", "/keys/sa.json")
        assert auth._service_account_path() == Path("/keys/sa.json")

    def test_defaults_under_config_dir(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._service_account_path() == Path("/tmp/cfg/service_account.json")

    def test_none_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._service_account_path() is None


# -- _is_interactive --


class TestIsInteractive:
    def test_reflects_stdin_isatty(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        assert auth._is_interactive() is True
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        assert auth._is_interactive() is False


# -- authenticate_oauth --


class TestAuthenticateOauth:
    def test_returns_none_when_config_unset(self, monkeypatch):
        # The key regression: this used to raise SystemExit before reaching ADC.
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth.authenticate_oauth() is None

    def test_returns_none_when_no_credentials_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        assert auth.authenticate_oauth() is None


# -- authenticate_service_account --


class TestAuthenticateServiceAccount:
    def test_none_when_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth.authenticate_service_account() is None

    def test_none_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", str(tmp_path / "absent.json"))
        assert auth.authenticate_service_account() is None


# -- authenticate (fallback chain) --


class TestAuthenticate:
    def test_prefers_oauth(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(auth, "authenticate_oauth", lambda scopes=None: sentinel)
        monkeypatch.setattr(
            auth,
            "authenticate_service_account",
            lambda scopes=None: pytest.fail("must not reach service account"),
        )
        assert auth.authenticate() is sentinel

    def test_falls_back_to_service_account(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(auth, "authenticate_oauth", lambda scopes=None: None)
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None: sentinel
        )
        monkeypatch.setattr(
            auth,
            "authenticate_adc",
            lambda scopes=None: pytest.fail("must not reach adc"),
        )
        assert auth.authenticate() is sentinel

    def test_falls_through_to_adc(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(auth, "authenticate_oauth", lambda scopes=None: None)
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None: None
        )
        monkeypatch.setattr(auth, "authenticate_adc", lambda scopes=None: sentinel)
        assert auth.authenticate() is sentinel

    def test_helpful_error_when_no_credentials(self, monkeypatch):
        monkeypatch.setattr(auth, "authenticate_oauth", lambda scopes=None: None)
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None: None
        )

        def raise_default_error(scopes=None):
            raise google.auth.exceptions.DefaultCredentialsError("none found")

        monkeypatch.setattr(auth, "authenticate_adc", raise_default_error)
        with pytest.raises(SystemExit, match="no Google Drive credentials found"):
            auth.authenticate()

    def test_helpful_error_on_any_google_auth_error_from_adc(self, monkeypatch):
        # Not only DefaultCredentialsError — any google.auth error (e.g. a stale
        # ADC refresh failure) yields the helpful message, not a raw traceback.
        monkeypatch.setattr(auth, "authenticate_oauth", lambda scopes=None: None)
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None: None
        )

        def raise_refresh_error(scopes=None):
            raise google.auth.exceptions.RefreshError("stale adc")

        monkeypatch.setattr(auth, "authenticate_adc", raise_refresh_error)
        with pytest.raises(SystemExit, match="no Google Drive credentials found"):
            auth.authenticate()


# -- authenticate_service_account (loads creds) --


class TestServiceAccountLoads:
    def test_loads_credentials_from_existing_file(self, monkeypatch, tmp_path):
        sa = tmp_path / "sa.json"
        sa.write_text("{}")
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", str(sa))
        sentinel = object()
        monkeypatch.setattr(
            "google.oauth2.service_account.Credentials.from_service_account_file",
            lambda path, scopes=None: sentinel,
        )
        assert auth.authenticate_service_account() is sentinel


# -- authenticate_oauth (loads token / runs flow) --


class TestAuthenticateOauthFlow:
    def test_returns_valid_token_without_flow(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_token.json").write_text("{}")
        creds = MagicMock(valid=True, expired=False)
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        assert auth.authenticate_oauth() is creds

    def test_runs_local_server_when_no_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        new_creds = MagicMock()
        new_creds.to_json.return_value = '{"token": "x"}'
        flow = MagicMock()
        flow.run_local_server.return_value = new_creds
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: flow,
        )
        result = auth.authenticate_oauth()
        assert result is new_creds
        assert (tmp_path / "gdrives_token.json").read_text() == '{"token": "x"}'
        flow.run_local_server.assert_called_once()

    def test_refreshes_expired_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        token = tmp_path / "gdrives_token.json"
        token.write_text("{}")
        creds = MagicMock(expired=True, valid=True)
        creds.refresh_token = "rt"
        creds.to_json.return_value = '{"refreshed": true}'
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        monkeypatch.setattr("google.auth.transport.requests.Request", lambda: None)
        result = auth.authenticate_oauth()
        assert result is creds
        creds.refresh.assert_called_once()
        assert token.read_text() == '{"refreshed": true}'

    def test_refresh_error_falls_through_to_flow(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        (tmp_path / "gdrives_token.json").write_text("{}")
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        stale = MagicMock(expired=True)
        stale.refresh_token = "rt"
        stale.refresh.side_effect = google.auth.exceptions.RefreshError("boom")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: stale,
        )
        monkeypatch.setattr("google.auth.transport.requests.Request", lambda: None)
        new_creds = MagicMock()
        new_creds.to_json.return_value = "{}"
        flow = MagicMock()
        flow.run_local_server.return_value = new_creds
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: flow,
        )
        assert auth.authenticate_oauth() is new_creds

    def test_network_failure_on_refresh_is_not_a_reconsent(self, monkeypatch, tmp_path):
        # Offline with a valid refresh token: report the network error rather
        # than opening a browser consent that can't help.
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        (tmp_path / "gdrives_token.json").write_text("{}")
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        stale = MagicMock(expired=True)
        stale.refresh_token = "rt"
        stale.refresh.side_effect = google.auth.exceptions.TransportError("offline")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: stale,
        )
        monkeypatch.setattr("google.auth.transport.requests.Request", lambda: None)
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: pytest.fail("must not start a consent flow"),
        )
        with pytest.raises(google.auth.exceptions.TransportError, match="offline"):
            auth.authenticate_oauth()

    def test_headless_no_token_returns_none(self, monkeypatch, tmp_path):
        # Credentials file present but no interactive terminal: fall through
        # (return None) instead of blocking on a browser flow that can't complete.
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: pytest.fail("must not start interactive flow"),
        )
        assert auth.authenticate_oauth() is None

    def test_token_lacking_requested_scopes_is_discarded_and_reconsented(
        self, monkeypatch, tmp_path
    ):
        # A token granted for another scope set must not be loaded (it would
        # 403 on first use); the flow runs again and the new grant is saved.
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        token = tmp_path / "gdrives_token_documents.json"
        token.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: pytest.fail("must not load a mismatched token"),
        )
        new_creds = MagicMock()
        new_creds.to_json.return_value = json.dumps({"scopes": auth.DOCS_WRITE_SCOPES})
        flow = MagicMock()
        flow.run_local_server.return_value = new_creds
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: flow,
        )
        assert auth.authenticate_oauth(auth.DOCS_WRITE_SCOPES) is new_creds
        assert json.loads(token.read_text())["scopes"] == auth.DOCS_WRITE_SCOPES

    def test_token_lacking_requested_scopes_headless_returns_none(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        token = tmp_path / "gdrives_token_documents.json"
        token.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: pytest.fail("must not load a mismatched token"),
        )
        assert auth.authenticate_oauth(auth.DOCS_WRITE_SCOPES) is None

    def test_write_token_swallows_oserror(self, tmp_path):
        creds = MagicMock()
        creds.to_json.return_value = "{}"
        unwritable = tmp_path / "missing" / "token.json"  # parent absent -> OSError
        auth._write_token(unwritable, creds)  # must not raise
        assert not unwritable.exists()

    def test_write_token_uses_owner_only_permissions(self, tmp_path):
        # The token holds a refresh token: it must not be group/world readable.
        creds = MagicMock()
        creds.to_json.return_value = '{"refresh_token": "secret"}'
        token = tmp_path / "gdrives_token.json"
        auth._write_token(token, creds)
        assert token.read_text() == '{"refresh_token": "secret"}'
        assert stat.S_IMODE(token.stat().st_mode) == 0o600


# -- authenticate_adc --


class TestAuthenticateAdc:
    def test_returns_default_credentials(self, monkeypatch):
        creds = object()
        monkeypatch.setattr("google.auth.default", lambda scopes=None: (creds, "proj"))
        assert auth.authenticate_adc() is creds


# -- build_drive_service / build_sheets_service / build_docs_service --


class TestBuildDriveService:
    def test_builds_v3_with_authenticated_creds(self, monkeypatch):
        creds = object()
        service = object()
        monkeypatch.setattr(auth, "authenticate", lambda scopes=None: creds)
        rec = {}
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda *a, **k: rec.update(a=a, k=k) or service,
        )
        assert auth.build_drive_service() is service
        assert rec["a"] == ("drive", "v3")
        assert rec["k"]["credentials"] is creds


class TestBuildSheetsService:
    def test_builds_v4_with_authenticated_creds(self, monkeypatch):
        creds = object()
        service = object()
        rec = {}
        monkeypatch.setattr(
            auth, "authenticate", lambda scopes=None: rec.update(scopes=scopes) or creds
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda *a, **k: rec.update(a=a, k=k) or service,
        )
        assert auth.build_sheets_service(auth.SHEETS_WRITE_SCOPES) is service
        assert rec["a"] == ("sheets", "v4")
        assert rec["k"]["credentials"] is creds
        assert rec["scopes"] == auth.SHEETS_WRITE_SCOPES


class TestBuildDocsService:
    def test_builds_v1_with_authenticated_creds(self, monkeypatch):
        creds = object()
        service = object()
        rec = {}
        monkeypatch.setattr(
            auth, "authenticate", lambda scopes=None: rec.update(scopes=scopes) or creds
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda *a, **k: rec.update(a=a, k=k) or service,
        )
        assert auth.build_docs_service(auth.DOCS_WRITE_SCOPES) is service
        assert rec["a"] == ("docs", "v1")
        assert rec["k"]["credentials"] is creds
        assert rec["scopes"] == auth.DOCS_WRITE_SCOPES


class TestBuildService:
    def test_same_scopes_authenticate_once(self, monkeypatch):
        # A path-based sheets-get resolves the path with a Drive client and
        # reads with a Sheets client: one authentication serves both.
        calls = []
        monkeypatch.setattr(
            auth, "authenticate", lambda scopes=None: calls.append(scopes) or object()
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda api, version, credentials: credentials,
        )
        drive = auth.build_drive_service()
        sheets = auth.build_sheets_service()
        assert drive is sheets  # the same credentials object
        assert calls == [auth.SCOPES]
        write = auth.build_sheets_service(auth.SHEETS_WRITE_SCOPES)
        assert write is not drive
        assert calls == [auth.SCOPES, auth.SHEETS_WRITE_SCOPES]

    def test_all_builders_share_one_path(self, monkeypatch):
        rec = []
        monkeypatch.setattr(auth, "authenticate", lambda scopes=None: "creds")
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda api, version, credentials: rec.append((api, version, credentials)),
        )
        auth.build_drive_service()
        auth.build_sheets_service()
        auth.build_docs_service()
        assert rec == [
            ("drive", "v3", "creds"),
            ("sheets", "v4", "creds"),
            ("docs", "v1", "creds"),
        ]


# -- _token_path scope split --


class TestTokenPathScopes:
    def test_readonly_default_uses_shared_token(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_path() == Path("/tmp/cfg/gdrives_token.json")
        assert auth._token_path(auth.SCOPES) == Path("/tmp/cfg/gdrives_token.json")

    def test_write_scope_uses_separate_token(self, monkeypatch):
        # Write access must not clobber (or re-consent) the read-only token.
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_path(auth.SHEETS_WRITE_SCOPES) == Path(
            "/tmp/cfg/gdrives_token_rw.json"
        )

    def test_docs_scope_uses_its_own_token(self, monkeypatch):
        # A Sheets-consented token does not carry the Docs scope; sharing one
        # file would 403 (or re-consent and clobber the Sheets grant).
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_path(auth.DOCS_WRITE_SCOPES) == Path(
            "/tmp/cfg/gdrives_token_documents.json"
        )

    def test_unknown_scope_set_gets_stable_sorted_name(self):
        scopes = [
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/documents",
        ]
        assert auth._token_name(scopes) == "gdrives_token_documents_drive-file.json"
        assert auth._token_name(list(reversed(scopes))) == auth._token_name(scopes)

    def test_none_when_config_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._token_path(auth.SHEETS_WRITE_SCOPES) is None


# -- _token_covers (granted-scope check) --


class TestTokenCovers:
    def _write(self, tmp_path, payload):
        token = tmp_path / "token.json"
        token.write_text(payload)
        return token

    def test_granted_superset_covers(self, tmp_path):
        token = self._write(
            tmp_path, json.dumps({"scopes": auth.DOCS_WRITE_SCOPES + auth.SCOPES})
        )
        assert auth._token_covers(token, auth.DOCS_WRITE_SCOPES) is True

    def test_mismatch_does_not_cover(self, tmp_path):
        token = self._write(tmp_path, json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        assert auth._token_covers(token, auth.DOCS_WRITE_SCOPES) is False

    def test_space_separated_scopes_string(self, tmp_path):
        token = self._write(
            tmp_path, json.dumps({"scopes": " ".join(auth.DOCS_WRITE_SCOPES)})
        )
        assert auth._token_covers(token, auth.DOCS_WRITE_SCOPES) is True

    @pytest.mark.parametrize(
        "payload",
        ["{}", json.dumps({"scopes": 5}), "not json", "[]"],
        ids=["no-scopes-entry", "non-list-scopes", "unparseable", "non-object"],
    )
    def test_unknown_grant_is_left_to_the_loader(self, tmp_path, payload):
        token = self._write(tmp_path, payload)
        assert auth._token_covers(token, auth.DOCS_WRITE_SCOPES) is True

    def test_missing_file_is_left_to_the_loader(self, tmp_path):
        assert auth._token_covers(tmp_path / "absent.json", auth.SCOPES) is True


def test_token_write_does_not_follow_a_planted_scratch_symlink(tmp_path, monkeypatch):
    victim = tmp_path / "unrelated"
    victim.write_text("keep")
    link = plant_scratch_symlink(monkeypatch, tmp_path, victim)
    token = tmp_path / "token.json"
    creds = MagicMock()
    creds.to_json.return_value = '{"token": "new"}'
    auth._write_token(token, creds)
    assert victim.read_text() == "keep"
    assert link.is_symlink()
    assert token.read_text() == '{"token": "new"}'
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


@pytest.mark.parametrize("payload", ["not json", "[]", "null", "{}"])
def test_invalid_oauth_cache_falls_back_without_deleting_it(
    monkeypatch, tmp_path, payload, caplog
):
    monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "_is_interactive", lambda: False)
    token = tmp_path / "gdrives_token.json"
    token.write_text(payload)
    sentinel = object()
    monkeypatch.setattr(
        auth, "authenticate_service_account", lambda scopes=None: sentinel
    )
    assert auth.authenticate() is sentinel
    assert token.read_text() == payload
    assert "could not load cached OAuth token" in caplog.text


def test_unhashable_cached_scope_does_not_crash_scope_check(tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"scopes": [{}]}))
    assert auth._token_covers(token, auth.SCOPES)


def test_env_file_is_found_from_the_working_directory(tmp_path, monkeypatch):
    # An installed tool's module lives in site-packages; the project's .env is
    # where the command runs. Pretend no tracer is active, since python-dotenv
    # falls back to the working directory under one (coverage, a debugger).
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / ".env").write_text("GDRIVES_DOTENV_PROBE=from-project\n")
    monkeypatch.chdir(project / "sub")
    monkeypatch.setattr(sys, "gettrace", lambda: None)
    monkeypatch.setenv("GDRIVES_DOTENV_PROBE", "")  # restored (unset) afterwards
    monkeypatch.delenv("GDRIVES_DOTENV_PROBE")
    auth._load_env()
    assert os.environ["GDRIVES_DOTENV_PROBE"] == "from-project"


def test_environment_wins_over_the_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GDRIVES_DOTENV_PROBE=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GDRIVES_DOTENV_PROBE", "from-environment")
    auth._load_env()
    assert os.environ["GDRIVES_DOTENV_PROBE"] == "from-environment"


# -- describe_credentials --


class TestDescribeCredentials:
    """``describe_credentials`` names the credential authenticate() would pick."""

    @pytest.fixture(autouse=True)
    def no_credentials(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        # Describing must never start a consent or touch the network.
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda *args, **kwargs: pytest.fail("must not start a consent flow"),
        )
        monkeypatch.setattr(
            "google.auth.default",
            lambda *args, **kwargs: pytest.fail("must not probe ADC"),
        )

    @staticmethod
    def cached_token(monkeypatch, tmp_path, creds, name="gdrives_token.json"):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        (tmp_path / name).write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )

    @staticmethod
    def service_account(monkeypatch, tmp_path, payload):
        key = tmp_path / "sa.json"
        key.write_text(payload)
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", str(key))
        return key

    def test_nothing_configured_is_adc(self):
        info = auth.describe_credentials()
        assert info == auth.CredentialInfo(kind="adc")
        assert str(info) == "Application Default Credentials"

    def test_valid_oauth_token(self, monkeypatch, tmp_path):
        creds = MagicMock(valid=True, expired=False)
        self.cached_token(monkeypatch, tmp_path, creds, "gdrives_token_rw.json")
        info = auth.describe_credentials(auth.SHEETS_WRITE_SCOPES)
        token = tmp_path / "gdrives_token_rw.json"
        assert info == auth.CredentialInfo(kind="oauth", source=token)
        assert str(info) == f"OAuth token {token}"
        creds.refresh.assert_not_called()

    def test_expired_oauth_token_is_refreshed_first(self, monkeypatch, tmp_path):
        creds = MagicMock(valid=False, expired=True, refresh_token="rt")
        self.cached_token(monkeypatch, tmp_path, creds)
        info = auth.describe_credentials()
        assert (info.kind, info.consent, info.refresh) == ("oauth", False, True)
        assert str(info).endswith("gdrives_token.json, refreshed first")
        creds.refresh.assert_not_called()

    def test_no_token_with_a_terminal_needs_consent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        secrets = tmp_path / "gdrives_credentials.json"
        secrets.write_text("{}")
        info = auth.describe_credentials()
        assert info == auth.CredentialInfo(kind="oauth", consent=True, source=secrets)
        assert str(info) == f"OAuth, after an interactive consent (client {secrets})"

    def test_unusable_token_with_a_terminal_needs_consent(self, monkeypatch, tmp_path):
        creds = MagicMock(valid=False, expired=True, refresh_token=None)
        self.cached_token(monkeypatch, tmp_path, creds)
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        assert auth.describe_credentials().consent is True

    def test_token_for_other_scopes_is_passed_over_quietly(
        self, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        token = tmp_path / "gdrives_token_documents.json"
        token.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        assert auth.describe_credentials(auth.DOCS_WRITE_SCOPES).consent is True
        assert caplog.text == ""

    def test_unloadable_token_is_passed_over_quietly(
        self, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_token.json").write_text("not json")
        assert auth.describe_credentials().kind == "adc"
        assert caplog.text == ""

    def test_headless_without_a_token_falls_to_the_service_account(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        key = self.service_account(
            monkeypatch,
            tmp_path,
            json.dumps(
                {
                    "client_email": "robot@example.iam.gserviceaccount.com",
                    "private_key": "-----BEGIN PRIVATE KEY-----secret",
                    "private_key_id": "keyid123",
                }
            ),
        )
        info = auth.describe_credentials()
        assert info == auth.CredentialInfo(
            kind="service_account",
            identity="robot@example.iam.gserviceaccount.com",
            source=key,
        )
        text = f"{info} {info!r}"
        assert "robot@example.iam.gserviceaccount.com" in text
        assert "secret" not in text and "keyid123" not in text

    def test_service_account_in_the_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "service_account.json").write_text(
            json.dumps({"client_email": "sa@example.com"})
        )
        info = auth.describe_credentials()
        assert (info.kind, info.identity) == ("service_account", "sa@example.com")

    @pytest.mark.parametrize("payload", ["not json", "[]", '{"client_email": 3}', "{}"])
    def test_unreadable_service_account_email(self, monkeypatch, tmp_path, payload):
        key = self.service_account(monkeypatch, tmp_path, payload)
        info = auth.describe_credentials()
        assert (info.kind, info.identity) == ("service_account", None)
        assert str(info) == f"service account (client_email unreadable) (key {key})"

    def test_missing_service_account_file_is_adc(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", str(tmp_path / "gone.json"))
        assert auth.describe_credentials().kind == "adc"

    def test_valid_token_wins_over_a_service_account(self, monkeypatch, tmp_path):
        self.cached_token(monkeypatch, tmp_path, MagicMock(valid=True, expired=False))
        self.service_account(monkeypatch, tmp_path, "{}")
        assert auth.describe_credentials().kind == "oauth"

    @pytest.mark.parametrize(
        ("token", "interactive", "service_account", "expected"),
        [
            ("valid", False, True, "oauth"),
            ("expired", False, True, "oauth"),
            ("dead", True, True, "oauth"),
            ("dead", False, True, "service_account"),
            (None, False, True, "service_account"),
            (None, False, False, "adc"),
        ],
    )
    def test_agrees_with_authenticate(
        self, monkeypatch, tmp_path, token, interactive, service_account, expected
    ):
        """The described kind is the one authenticate() then returns."""
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: interactive)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        kinds = {"valid": (True, False, None), "expired": (False, True, "rt")}
        valid, expired, refresh = kinds.get(token, (False, False, None))
        creds = MagicMock(valid=valid, expired=expired, refresh_token=refresh)
        creds.refresh.side_effect = lambda request: setattr(creds, "valid", True)
        if token is not None:
            (tmp_path / "gdrives_token.json").write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        if service_account:
            self.service_account(monkeypatch, tmp_path, "{}")
        described = auth.describe_credentials().kind

        consented = MagicMock()
        consented.to_json.return_value = "{}"
        creds.to_json.return_value = "{}"
        flow = MagicMock()
        flow.run_local_server.return_value = consented
        monkeypatch.setattr(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            lambda path, scopes=None: flow,
        )
        monkeypatch.setattr("google.auth.transport.requests.Request", lambda: None)
        monkeypatch.setattr(
            "google.oauth2.service_account.Credentials.from_service_account_file",
            lambda path, scopes=None: "service_account",
        )
        monkeypatch.setattr(auth, "authenticate_adc", lambda scopes=None: "adc")
        used = auth.authenticate()
        actual = used if isinstance(used, str) else "oauth"
        assert described == expected == actual
