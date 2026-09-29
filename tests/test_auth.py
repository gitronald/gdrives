"""Tests for gdrives.auth — credential discovery and the fallback chain."""

import dataclasses
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


class TestCredentialsPath:
    def test_none_when_config_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._credentials_path() is None

    def test_path_under_config_dir(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
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
        monkeypatch.setattr(
            auth, "authenticate_oauth", lambda scopes=None, **kwargs: sentinel
        )
        monkeypatch.setattr(
            auth,
            "authenticate_service_account",
            lambda scopes=None, **kwargs: pytest.fail("must not reach service account"),
        )
        assert auth.authenticate() is sentinel

    def test_falls_back_to_service_account(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(
            auth, "authenticate_oauth", lambda scopes=None, **kwargs: None
        )
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None, **kwargs: sentinel
        )
        monkeypatch.setattr(
            auth,
            "authenticate_adc",
            lambda scopes=None, **kwargs: pytest.fail("must not reach adc"),
        )
        assert auth.authenticate() is sentinel

    def test_falls_through_to_adc(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(
            auth, "authenticate_oauth", lambda scopes=None, **kwargs: None
        )
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None, **kwargs: None
        )
        monkeypatch.setattr(
            auth, "authenticate_adc", lambda scopes=None, **kwargs: sentinel
        )
        assert auth.authenticate() is sentinel

    def test_helpful_error_when_no_credentials(self, monkeypatch):
        monkeypatch.setattr(
            auth, "authenticate_oauth", lambda scopes=None, **kwargs: None
        )
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None, **kwargs: None
        )

        def raise_default_error(scopes=None, **kwargs):
            raise google.auth.exceptions.DefaultCredentialsError("none found")

        monkeypatch.setattr(auth, "authenticate_adc", raise_default_error)
        with pytest.raises(SystemExit, match="no Google Drive credentials found"):
            auth.authenticate()

    def test_helpful_error_on_any_google_auth_error_from_adc(self, monkeypatch):
        # Not only DefaultCredentialsError — any google.auth error (e.g. a stale
        # ADC refresh failure) yields the helpful message, not a raw traceback.
        monkeypatch.setattr(
            auth, "authenticate_oauth", lambda scopes=None, **kwargs: None
        )
        monkeypatch.setattr(
            auth, "authenticate_service_account", lambda scopes=None, **kwargs: None
        )

        def raise_refresh_error(scopes=None, **kwargs):
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

    def test_token_lacking_requested_scopes_is_passed_over_and_reconsented(
        self, monkeypatch, tmp_path, caplog
    ):
        # A token granted for another scope set must not be loaded (it would
        # 403 on first use), so the flow runs again. Its file is the only
        # place a documents token has, and it holds a grant the new one does
        # not include: the new token is used for this run and not saved.
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
        assert json.loads(token.read_text())["scopes"] == auth.SHEETS_WRITE_SCOPES
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "gdrives_credentials.json",
            "gdrives_token_documents.json",
        ]
        assert f"OAuth token not saved: {token} holds a grant" in caplog.text

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
        monkeypatch.setattr(
            "google.auth.default", lambda scopes=None, **kwargs: (creds, "proj")
        )
        assert auth.authenticate_adc() is creds


# -- build_drive_service / build_sheets_service / build_docs_service --


class TestBuildDriveService:
    def test_builds_v3_with_authenticated_creds(self, monkeypatch):
        creds = object()
        service = object()
        monkeypatch.setattr(auth, "authenticate", lambda scopes=None, **kwargs: creds)
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
            auth,
            "authenticate",
            lambda scopes=None, **kwargs: rec.update(scopes=scopes) or creds,
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
            auth,
            "authenticate",
            lambda scopes=None, **kwargs: rec.update(scopes=scopes) or creds,
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
            auth,
            "authenticate",
            lambda scopes=None, **kwargs: calls.append(scopes) or object(),
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
        monkeypatch.setattr(auth, "authenticate", lambda scopes=None, **kwargs: "creds")
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


# -- _token_name scope split --


class TestTokenName:
    def test_known_scope_sets_keep_their_historical_names(self):
        assert auth._token_name(auth.SCOPES) == "gdrives_token.json"
        # Write access must not clobber (or re-consent) the read-only token.
        assert auth._token_name(auth.SHEETS_WRITE_SCOPES) == "gdrives_token_rw.json"

    def test_docs_scope_uses_its_own_token(self):
        # A Sheets-consented token does not carry the Docs scope; sharing one
        # file would 403 (or re-consent and clobber the Sheets grant).
        assert (
            auth._token_name(auth.DOCS_WRITE_SCOPES) == "gdrives_token_documents.json"
        )

    def test_unknown_scope_set_gets_stable_sorted_name(self):
        scopes = [
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/documents",
        ]
        assert auth._token_name(scopes) == "gdrives_token_documents_drive-file.json"
        assert auth._token_name(list(reversed(scopes))) == auth._token_name(scopes)


# -- _recorded_scopes (the grant a token file records) --


class TestRecordedScopes:
    def _write(self, tmp_path, payload):
        token = tmp_path / "token.json"
        token.write_text(payload)
        return token

    def test_a_list_of_scopes(self, tmp_path):
        granted = auth.DOCS_WRITE_SCOPES + auth.SCOPES
        token = self._write(tmp_path, json.dumps({"scopes": granted}))
        assert auth._recorded_scopes(token) == granted

    def test_space_separated_scopes_string(self, tmp_path):
        granted = auth.DOCS_WRITE_SCOPES + auth.SCOPES
        token = self._write(tmp_path, json.dumps({"scopes": " ".join(granted)}))
        assert auth._recorded_scopes(token) == granted

    @pytest.mark.parametrize(
        "payload",
        ["{}", json.dumps({"scopes": 5}), "not json", "[]"],
        ids=["no-scopes-entry", "non-list-scopes", "unparseable", "non-object"],
    )
    def test_unknown_grant_records_none(self, tmp_path, payload):
        assert auth._recorded_scopes(self._write(tmp_path, payload)) is None

    def test_missing_file_records_none(self, tmp_path):
        assert auth._recorded_scopes(tmp_path / "absent.json") is None


class TestLoadTokenChecksTheGrant:
    """``_load_token`` passes over a grant that misses the request, and only that."""

    @pytest.fixture
    def loaded(self, monkeypatch):
        creds = MagicMock()
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        return creds

    def test_mismatch_is_passed_over(self, tmp_path, loaded, caplog):
        token = tmp_path / "token.json"
        token.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        assert auth._load_token(token, auth.DOCS_WRITE_SCOPES) is None
        assert "does not cover the requested scopes" in caplog.text

    @pytest.mark.parametrize(
        "payload",
        [json.dumps({"scopes": auth.DOCS_WRITE_SCOPES + auth.SCOPES}), "{}", "[]"],
        ids=["superset", "no-scopes-entry", "non-object"],
    )
    def test_a_covering_or_unknown_grant_is_left_to_the_loader(
        self, tmp_path, loaded, payload
    ):
        token = tmp_path / "token.json"
        token.write_text(payload)
        assert auth._load_token(token, auth.DOCS_WRITE_SCOPES) is loaded


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
        auth, "authenticate_service_account", lambda scopes=None, **kwargs: sentinel
    )
    assert auth.authenticate() is sentinel
    assert token.read_text() == payload
    assert "could not load cached OAuth token" in caplog.text


def test_unhashable_cached_scope_does_not_crash_scope_check(tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"scopes": [{}]}))
    assert auth._recorded_scopes(token) is None


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
        monkeypatch.setattr(
            auth, "authenticate_adc", lambda scopes=None, **kwargs: "adc"
        )
        used = auth.authenticate()
        actual = used if isinstance(used, str) else "oauth"
        assert described == expected == actual


# -- a broader grant covers a narrower request --

AUTH = "https://www.googleapis.com/auth/"
IMPLIED_PAIRS = [
    (broad, narrow) for broad, served in auth._IMPLIES.items() for narrow in served
]


class TestImpliedScopes:
    def test_the_table_is_the_confirmed_pairs(self):
        assert {
            broad.removeprefix(AUTH): sorted(n.removeprefix(AUTH) for n in served)
            for broad, served in auth._IMPLIES.items()
        } == {
            "drive": [
                "documents",
                "documents.readonly",
                "drive.file",
                "drive.metadata",
                "drive.metadata.readonly",
                "drive.readonly",
                "spreadsheets",
                "spreadsheets.readonly",
            ],
            "drive.metadata": ["drive.metadata.readonly"],
            "spreadsheets": ["spreadsheets.readonly"],
            "documents": ["documents.readonly"],
        }

    @pytest.mark.parametrize(("broad", "narrow"), IMPLIED_PAIRS)
    def test_a_grant_covers_each_scope_it_implies(self, broad, narrow):
        assert auth._covers([broad], [narrow]) is True
        assert auth._covers([narrow], [broad]) is False

    def test_a_grant_covers_itself_and_a_mixed_request(self):
        assert auth._covers(auth.SCOPES, auth.SCOPES) is True
        assert auth._covers(
            auth.DRIVE_WRITE_SCOPES, auth.SHEETS_WRITE_SCOPES + auth.DOCS_WRITE_SCOPES
        )

    @pytest.mark.parametrize(
        ("granted", "requested"),
        [
            (auth.SHEETS_WRITE_SCOPES, auth.DOCS_WRITE_SCOPES),
            (auth.SCOPES, auth.SHEETS_WRITE_SCOPES),
            # Accepted by the Sheets methods, but only for the app's own files.
            ([AUTH + "drive.file"], auth.SHEETS_WRITE_SCOPES),
            (auth.SHEETS_WRITE_SCOPES, auth.SHEETS_WRITE_SCOPES + auth.SCOPES),
        ],
    )
    def test_a_grant_that_does_not_cover(self, granted, requested):
        assert auth._covers(granted, requested) is False


def grant(path: Path, scopes: list[str] | None) -> str:
    """Write a token file recording ``scopes`` at ``path``; return its content."""
    content = json.dumps({"refresh_token": "theirs", "scopes": scopes})
    path.write_text(content)
    return content


@pytest.fixture
def oauth(monkeypatch, tmp_path):
    """A config dir with client secrets, a terminal, and recorded fakes.

    ``loaded`` lists each ``(file name, scopes)`` a token was loaded with, and
    ``tokens`` maps a file name to the credentials loading it returns (valid
    ones by default). ``flow`` is the consent flow; its token is ``consented``.
    """
    monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
    monkeypatch.setattr(auth, "_is_interactive", lambda: True)
    (tmp_path / "gdrives_credentials.json").write_text("{}")
    env = MagicMock()
    env.dir = tmp_path
    env.loaded = []
    env.tokens = {}
    env.consented.to_json.return_value = json.dumps({"refresh_token": "new"})
    env.flow.run_local_server.return_value = env.consented

    def load(path, scopes=None):
        name = Path(path).name
        env.loaded.append((name, scopes))
        return env.tokens.get(name, MagicMock(valid=True, expired=False))

    def start(path, scopes=None):
        env.flow_scopes = scopes
        return env.flow

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file", load
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", start
    )
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: None)
    return env


def dead_token() -> MagicMock:
    """Credentials whose refresh Google refuses: the grant was revoked."""
    creds = MagicMock(valid=False, expired=True, refresh_token="rt")
    creds.refresh.side_effect = google.auth.exceptions.RefreshError("revoked")
    return creds


class TestBroaderGrantIsUsed:
    def test_a_callers_drive_token_serves_a_sheets_request(self, oauth):
        # The upgrade hazard: a caller wrote its own token, granted `drive`,
        # under the name gdrives later claimed for the `spreadsheets` scope.
        token = oauth.dir / "gdrives_token_rw.json"
        content = grant(token, auth.DRIVE_WRITE_SCOPES)
        creds = auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES)
        assert creds is not oauth.consented
        oauth.flow.run_local_server.assert_not_called()
        assert token.read_text() == content
        # Loaded with the scopes it records, not the ones requested.
        assert oauth.loaded == [("gdrives_token_rw.json", auth.DRIVE_WRITE_SCOPES)]

    def test_a_grant_that_lists_the_request_is_loaded_with_the_request(self, oauth):
        grant(oauth.dir / "gdrives_token.json", auth.SCOPES + auth.DOCS_WRITE_SCOPES)
        auth.authenticate_oauth()
        assert oauth.loaded == [("gdrives_token.json", auth.SCOPES)]

    def test_described_without_a_consent(self, oauth):
        token = oauth.dir / "gdrives_token_rw.json"
        grant(token, auth.DRIVE_WRITE_SCOPES)
        info = auth.describe_credentials(auth.SHEETS_WRITE_SCOPES)
        assert info == auth.CredentialInfo(kind="oauth", source=token)


# -- no consent overwrites a grant it does not include --


class TestTokenPaths:
    def test_historical_name_then_derived_name(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_paths(auth.SHEETS_WRITE_SCOPES) == [
            Path("/tmp/cfg/gdrives_token_rw.json"),
            Path("/tmp/cfg/gdrives_token_spreadsheets.json"),
        ]
        assert auth._token_paths(auth.SCOPES) == [
            Path("/tmp/cfg/gdrives_token.json"),
            Path("/tmp/cfg/gdrives_token_drive-readonly.json"),
        ]

    def test_one_place_when_the_name_is_the_derived_one(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", "/tmp/cfg")
        assert auth._token_paths(auth.DOCS_WRITE_SCOPES) == [
            Path("/tmp/cfg/gdrives_token_documents.json")
        ]

    def test_none_when_config_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        assert auth._token_paths(auth.SCOPES) == []


class TestConsentKeepsOtherGrants:
    """A consent for ``spreadsheets`` and what ``gdrives_token_rw.json`` holds."""

    def consent(self, oauth):
        assert auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES) is oauth.consented
        return {
            path.name: path.read_text()
            for path in oauth.dir.iterdir()
            if path.name.startswith("gdrives_token")
        }

    @pytest.mark.parametrize(
        "held",
        [
            json.dumps({"refresh_token": "theirs", "scopes": [AUTH + "drive"]}),
            json.dumps({"refresh_token": "theirs", "scopes": [AUTH + "documents"]}),
            json.dumps({"refresh_token": "theirs"}),
            "not json",
        ],
        ids=["broader", "unrelated", "no-recorded-scopes", "unparseable"],
    )
    def test_a_grant_it_does_not_include_is_left_alone(self, oauth, caplog, held):
        historical = oauth.dir / "gdrives_token_rw.json"
        historical.write_text(held)
        oauth.tokens["gdrives_token_rw.json"] = dead_token()
        assert self.consent(oauth) == {
            "gdrives_token_rw.json": held,
            "gdrives_token_spreadsheets.json": '{"refresh_token": "new"}',
        }
        derived = oauth.dir / "gdrives_token_spreadsheets.json"
        assert stat.S_IMODE(derived.stat().st_mode) == 0o600
        assert f"OAuth token written to {derived}: {historical} holds a grant" in (
            caplog.text
        )

    @pytest.mark.parametrize(
        "held", [[AUTH + "spreadsheets"], [AUTH + "spreadsheets.readonly"], []]
    )
    def test_a_grant_it_includes_is_replaced(self, oauth, caplog, held):
        grant(oauth.dir / "gdrives_token_rw.json", held)
        oauth.tokens["gdrives_token_rw.json"] = dead_token()
        assert self.consent(oauth) == {
            "gdrives_token_rw.json": '{"refresh_token": "new"}'
        }
        assert "holds a grant" not in caplog.text

    def test_no_file_yet_takes_the_historical_name(self, oauth):
        assert self.consent(oauth) == {
            "gdrives_token_rw.json": '{"refresh_token": "new"}'
        }

    def test_both_places_taken_saves_nothing(self, oauth, caplog):
        theirs = grant(oauth.dir / "gdrives_token_rw.json", [AUTH + "documents"])
        also = grant(oauth.dir / "gdrives_token_spreadsheets.json", [AUTH + "drive"])
        oauth.tokens["gdrives_token_spreadsheets.json"] = dead_token()
        assert self.consent(oauth) == {
            "gdrives_token_rw.json": theirs,
            "gdrives_token_spreadsheets.json": also,
        }
        assert "OAuth token not saved" in caplog.text


class TestTokenLookupOrder:
    def test_the_historical_name_is_used_first(self, oauth):
        grant(oauth.dir / "gdrives_token_rw.json", auth.SHEETS_WRITE_SCOPES)
        grant(oauth.dir / "gdrives_token_spreadsheets.json", auth.SHEETS_WRITE_SCOPES)
        auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES)
        assert [name for name, _ in oauth.loaded] == ["gdrives_token_rw.json"]

    def test_a_token_written_under_the_derived_name_is_found(self, oauth):
        # The run after a consent that left the historical file alone.
        theirs = grant(oauth.dir / "gdrives_token_rw.json", [AUTH + "documents"])
        derived = oauth.dir / "gdrives_token_spreadsheets.json"
        grant(derived, auth.SHEETS_WRITE_SCOPES)
        ours = oauth.tokens["gdrives_token_spreadsheets.json"] = MagicMock(
            valid=True, expired=False
        )
        assert auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES) is ours
        assert [name for name, _ in oauth.loaded] == ["gdrives_token_spreadsheets.json"]
        oauth.flow.run_local_server.assert_not_called()
        assert (oauth.dir / "gdrives_token_rw.json").read_text() == theirs
        info = auth.describe_credentials(auth.SHEETS_WRITE_SCOPES)
        assert info == auth.CredentialInfo(kind="oauth", source=derived)

    def test_a_refused_refresh_moves_on_to_the_derived_name(self, oauth):
        theirs = grant(oauth.dir / "gdrives_token_rw.json", auth.DRIVE_WRITE_SCOPES)
        grant(oauth.dir / "gdrives_token_spreadsheets.json", auth.SHEETS_WRITE_SCOPES)
        oauth.tokens["gdrives_token_rw.json"] = dead_token()
        ours = oauth.tokens["gdrives_token_spreadsheets.json"] = MagicMock(
            valid=True, expired=False
        )
        assert auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES) is ours
        oauth.flow.run_local_server.assert_not_called()
        assert (oauth.dir / "gdrives_token_rw.json").read_text() == theirs

    def test_a_refreshed_token_goes_back_to_the_file_it_came_from(self, oauth):
        grant(oauth.dir / "gdrives_token_rw.json", [AUTH + "documents"])
        derived = oauth.dir / "gdrives_token_spreadsheets.json"
        grant(derived, auth.SHEETS_WRITE_SCOPES)
        stale = MagicMock(valid=True, expired=True, refresh_token="rt")
        stale.to_json.return_value = '{"refreshed": true}'
        oauth.tokens["gdrives_token_spreadsheets.json"] = stale
        assert auth.authenticate_oauth(auth.SHEETS_WRITE_SCOPES) is stale
        assert derived.read_text() == '{"refreshed": true}'

    def test_a_token_that_cannot_be_used_or_refreshed_is_passed_over(self, oauth):
        grant(oauth.dir / "gdrives_token.json", auth.SCOPES)
        oauth.tokens["gdrives_token.json"] = MagicMock(
            valid=False, expired=True, refresh_token=None
        )
        assert auth.authenticate_oauth() is oauth.consented


# -- a consent without a terminal --


class TestForce:
    @pytest.fixture(autouse=True)
    def headless(self, monkeypatch, oauth):
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)

    def test_the_consent_runs_without_a_terminal(self, oauth):
        assert auth.authenticate_oauth(force=True) is oauth.consented
        assert oauth.flow_scopes == auth.SCOPES
        oauth.flow.run_local_server.assert_called_once_with(
            port=0, open_browser=False, timeout_seconds=None
        )
        assert (oauth.dir / "gdrives_token.json").exists()

    def test_the_consent_runs_with_a_terminal(self, monkeypatch, oauth):
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        assert auth.authenticate_oauth(force=True) is oauth.consented

    def test_without_force_there_is_no_consent(self, oauth):
        assert auth.authenticate_oauth() is None
        oauth.flow.run_local_server.assert_not_called()

    def test_a_cached_token_is_still_used(self, oauth):
        grant(oauth.dir / "gdrives_token.json", auth.SCOPES)
        assert auth.authenticate_oauth(force=True) is not oauth.consented
        oauth.flow.run_local_server.assert_not_called()

    def test_no_client_secrets_is_an_error(self, oauth):
        secrets = oauth.dir / "gdrives_credentials.json"
        secrets.unlink()
        with pytest.raises(auth.ConsentError, match="no OAuth client secrets at"):
            auth.authenticate_oauth(force=True)

    def test_no_config_dir_is_an_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR")
        with pytest.raises(auth.ConsentError, match="GOOGLE_CONFIG_DIR is not set"):
            auth.authenticate_oauth(force=True)

    def test_authenticate_does_not_fall_through(self, monkeypatch, oauth):
        (oauth.dir / "gdrives_credentials.json").unlink()
        monkeypatch.setattr(
            auth,
            "authenticate_service_account",
            lambda scopes=None: pytest.fail("must not reach service account"),
        )
        with pytest.raises(auth.ConsentError):
            auth.authenticate(force=True)

    def test_authenticate_and_the_builders_pass_it_on(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            auth,
            "authenticate_oauth",
            lambda scopes=None, *, force=False: seen.append((scopes, force)) or "creds",
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda api, version, credentials: (api, credentials),
        )
        assert auth.authenticate(auth.SCOPES, force=True) == "creds"
        assert auth.build_drive_service(force=True) == ("drive", "creds")
        assert auth.build_sheets_service(auth.SHEETS_WRITE_SCOPES, force=True)
        assert auth.build_docs_service(auth.DOCS_WRITE_SCOPES, force=True)
        # Not served from the credentials an unforced call cached, or the reverse.
        assert auth.build_drive_service() == ("drive", "creds")
        assert seen == [
            (auth.SCOPES, True),
            (auth.SCOPES, True),
            (auth.SHEETS_WRITE_SCOPES, True),
            (auth.DOCS_WRITE_SCOPES, True),
            (auth.SCOPES, False),
        ]

    def test_described_as_a_consent(self, oauth):
        assert auth.describe_credentials().kind == "adc"
        info = auth.describe_credentials(force=True)
        assert (info.kind, info.consent) == ("oauth", True)

    @pytest.mark.parametrize("missing", ["client secrets", "config dir"])
    def test_described_as_the_error_authenticate_raises(
        self, monkeypatch, oauth, missing
    ):
        # Not as the service account or ADC a forced call never falls through to.
        if missing == "config dir":
            monkeypatch.delenv("GOOGLE_CONFIG_DIR")
        else:
            (oauth.dir / "gdrives_credentials.json").unlink()
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_PATH", str(oauth.dir / "sa.json"))
        (oauth.dir / "sa.json").write_text("{}")
        assert auth.describe_credentials().kind == "service_account"
        with pytest.raises(auth.ConsentError) as described:
            auth.describe_credentials(force=True)
        with pytest.raises(auth.ConsentError) as raised:
            auth.authenticate(force=True)
        assert str(described.value) == str(raised.value)


class TestConsentPrompt:
    def test_the_url_is_flushed_before_the_flow_waits(self, monkeypatch, oauth):
        # With no terminal stdout is block-buffered: unflushed, the URL would
        # reach the person only after the flow had stopped waiting for them.
        events = []
        stdout = MagicMock()
        stdout.write.side_effect = lambda text: events.append(text) or len(text)
        stdout.flush.side_effect = lambda: events.append("(flush)")
        monkeypatch.setattr(sys, "stdout", stdout)

        def run(**kwargs):
            sys.stdout.write("Please visit this URL")
            events.append("(waiting)")
            sys.stdout.flush()
            return oauth.consented

        oauth.flow.run_local_server.side_effect = run
        assert auth.authenticate_oauth(force=True) is oauth.consented
        assert events == ["Please visit this URL", "(flush)", "(waiting)", "(flush)"]
        assert sys.stdout is stdout


class TestConsentTimeout:
    def test_the_timeout_is_handed_to_the_flow(self, oauth):
        auth.authenticate_oauth(timeout=90)
        oauth.flow.run_local_server.assert_called_once_with(
            port=0, open_browser=False, timeout_seconds=90
        )

    @pytest.mark.parametrize("error", ["WSGITimeoutError", "AttributeError"])
    def test_running_out_raises_and_touches_no_token(self, monkeypatch, oauth, error):
        import google_auth_oauthlib.flow

        theirs = grant(oauth.dir / "gdrives_token.json", [AUTH + "documents"])
        if error == "AttributeError":
            # A release up to 1.2.1, which has no WSGITimeoutError to raise.
            monkeypatch.delattr(
                google_auth_oauthlib.flow, "WSGITimeoutError", raising=False
            )
        raised = getattr(google_auth_oauthlib.flow, error, AttributeError)
        oauth.flow.run_local_server.side_effect = raised("timed out")
        with pytest.raises(auth.ConsentError, match="no consent within 5 seconds"):
            auth.authenticate_oauth(force=True, timeout=5)
        assert sorted(path.name for path in oauth.dir.iterdir()) == [
            "gdrives_credentials.json",
            "gdrives_token.json",
        ]
        assert (oauth.dir / "gdrives_token.json").read_text() == theirs

    def test_an_attribute_error_without_a_timeout_is_not_one(self, oauth):
        oauth.flow.run_local_server.side_effect = AttributeError("a bug")
        with pytest.raises(AttributeError, match="a bug"):
            auth.authenticate_oauth()

    def test_another_attribute_error_is_not_reported_as_a_timeout(self, oauth):
        import google_auth_oauthlib.flow

        assert hasattr(google_auth_oauthlib.flow, "WSGITimeoutError")
        oauth.flow.run_local_server.side_effect = AttributeError("a bug")
        with pytest.raises(AttributeError, match="a bug"):
            auth.authenticate_oauth(force=True, timeout=5)
        assert not (oauth.dir / "gdrives_token.json").exists()

    def test_a_timeout_error_without_a_timeout_is_raised_as_it_is(self, oauth):
        import google_auth_oauthlib.flow

        error = google_auth_oauthlib.flow.WSGITimeoutError("timed out")
        oauth.flow.run_local_server.side_effect = error
        with pytest.raises(AttributeError, match="timed out"):
            auth.authenticate_oauth()


# -- announce_credentials --

STATES = [
    (auth.CredentialInfo(kind="oauth", source=Path("token.json")), False),
    (auth.CredentialInfo(kind="oauth", refresh=True, source=Path("token.json")), True),
    (auth.CredentialInfo(kind="oauth", consent=True, source=Path("c.json")), True),
    (auth.CredentialInfo(kind="service_account", identity="sa@example.com"), False),
    (auth.CredentialInfo(kind="adc"), False),
]


class TestAnnounceCredentials:
    @pytest.fixture
    def described(self, monkeypatch):
        """Fix what describe_credentials reports; record what it was asked."""
        state = MagicMock()
        state.info = auth.CredentialInfo(kind="adc")
        state.asked = []

        def describe(scopes=None, *, force=False):
            state.asked.append((scopes, force))
            return state.info

        monkeypatch.setattr(auth, "describe_credentials", describe)
        monkeypatch.setattr(
            auth, "authenticate", lambda scopes=None, *, force=False: "creds"
        )
        monkeypatch.setattr(
            "googleapiclient.discovery.build", lambda api, version, credentials: api
        )
        return state

    @pytest.mark.parametrize(("info", "waits"), STATES)
    def test_said_when_a_consent_or_a_refresh_is_coming(
        self, described, capsys, info, waits
    ):
        described.info = info
        auth.announce_credentials(auth.SHEETS_WRITE_SCOPES)
        assert capsys.readouterr() == ("", f"Credential: {info}\n" if waits else "")
        assert described.asked == [(auth.SHEETS_WRITE_SCOPES, False)]

    @pytest.mark.parametrize(("info", "waits"), STATES)
    def test_always_says_it(self, described, capsys, info, waits):
        described.info = info
        auth.announce_credentials(always=True, force=True)
        assert capsys.readouterr() == ("", f"Credential: {info}\n")
        assert described.asked == [(None, True)]

    @pytest.mark.parametrize(
        "info",
        [
            auth.CredentialInfo(
                kind="service_account",
                identity="sa@example.com",
                source=Path("key.json"),
                consent_skipped=True,
            ),
            auth.CredentialInfo(kind="adc", consent_skipped=True),
        ],
    )
    def test_a_skipped_consent_is_said_with_the_reason(self, described, capsys, info):
        described.info = info
        line = f"Credential: {info} ({auth.FALLBACK_REASON})\n"
        auth.announce_credentials()
        assert capsys.readouterr() == ("", line)
        auth.announce_credentials(always=True)
        assert capsys.readouterr() == ("", line)
        assert str(info) == str(dataclasses.replace(info, consent_skipped=False))

    def test_the_reason_reads_as_the_plan_words_it(self):
        info = auth.CredentialInfo(
            kind="service_account", identity="sa@example.com", consent_skipped=True
        )
        assert auth.credential_line(info).endswith(
            "(OAuth is configured, but no cached token serves these scopes and "
            "there is no terminal for a consent; run gdrives login)"
        )

    def test_a_skipped_consent_is_said_once_inside_the_block(self, described, capsys):
        described.info = info = auth.CredentialInfo(kind="adc", consent_skipped=True)
        with auth.announcing_credentials():
            auth.build_drive_service()
            auth.build_sheets_service()
        assert capsys.readouterr().err == auth.credential_line(info) + "\n"

    def test_no_oauth_client_stays_quiet(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "service_account.json").write_text('{"client_email": "a@b.c"}')
        auth.announce_credentials()
        assert capsys.readouterr() == ("", "")

    def test_an_oauth_client_without_a_terminal_says_why(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        (tmp_path / "service_account.json").write_text('{"client_email": "a@b.c"}')
        auth.announce_credentials()
        err = capsys.readouterr().err
        assert err.startswith("Credential: service account a@b.c (key ")
        assert err.endswith(f"({auth.FALLBACK_REASON})\n")

    def test_a_library_caller_hears_nothing_from_a_builder(self, described, capsys):
        described.info = auth.CredentialInfo(kind="oauth", consent=True)
        auth.build_drive_service()
        assert capsys.readouterr() == ("", "")
        assert described.asked == []

    def test_a_builder_announces_inside_the_block(self, described, capsys):
        described.info = info = auth.CredentialInfo(kind="oauth", consent=True)
        with auth.announcing_credentials():
            auth.build_drive_service()
            auth.build_sheets_service()  # the same scopes: authenticated once
            auth.build_sheets_service(auth.SHEETS_WRITE_SCOPES, force=True)
        assert capsys.readouterr().err == f"Credential: {info}\n" * 2
        assert described.asked == [
            (auth.SCOPES, False),
            (auth.SHEETS_WRITE_SCOPES, True),
        ]
        auth.build_docs_service(auth.DOCS_WRITE_SCOPES)  # the block is over
        assert capsys.readouterr().err == ""

    def test_a_builder_says_nothing_when_nothing_waits(self, described, capsys):
        with auth.announcing_credentials():
            auth.build_drive_service()
        assert capsys.readouterr() == ("", "")

    def test_a_line_already_printed_is_not_printed_again(self, described, capsys):
        described.info = info = auth.CredentialInfo(kind="oauth", refresh=True)
        with auth.announcing_credentials():
            auth.announce_credentials(auth.SHEETS_WRITE_SCOPES, always=True)
            auth.build_sheets_service(auth.SHEETS_WRITE_SCOPES)
            auth.announce_credentials(auth.SHEETS_WRITE_SCOPES, always=True)
        assert capsys.readouterr().err == f"Credential: {info}\n"


# -- CredentialInfo's new fields: oauth_client, terminal, consent_skipped, --
# -- service_account, and passed_over --


class TestCredentialInfoNewFieldsIgnoredByEquality:
    """The new fields have defaults and stay out of equality and __str__."""

    def test_str_and_equality_ignore_the_new_fields(self):
        base = auth.CredentialInfo(kind="oauth", source=Path("t.json"))
        decorated = auth.CredentialInfo(
            kind="oauth",
            source=Path("t.json"),
            oauth_client=Path("c.json"),
            terminal=True,
            consent_skipped=True,
            service_account=Path("sa.json"),
            passed_over=(auth.PassedToken(path=Path("x.json"), reason="missing"),),
        )
        assert base == decorated
        assert str(base) == str(decorated)
        assert hash(base) == hash(decorated)

    def test_a_bare_construction_still_has_the_old_defaults(self):
        info = auth.CredentialInfo(kind="adc")
        assert (info.oauth_client, info.terminal, info.consent_skipped) == (
            None,
            False,
            False,
        )
        assert (info.service_account, info.passed_over) == (None, ())


class TestCredentialInfoDetailFields:
    """Each new field, over each branch of describe_credentials()."""

    @pytest.fixture(autouse=True)
    def no_credentials(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)

    def test_adc_when_nothing_is_configured(self):
        info = auth.describe_credentials()
        assert info.kind == "adc"
        assert info.oauth_client is None
        assert info.terminal is False
        assert info.consent_skipped is False
        assert info.service_account is None
        assert info.passed_over == ()

    def test_a_cached_token(self, monkeypatch, tmp_path):
        creds = MagicMock(valid=True, expired=False)
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        secrets = tmp_path / "gdrives_credentials.json"
        secrets.write_text("{}")
        (tmp_path / "gdrives_token.json").write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        info = auth.describe_credentials()
        assert info.kind == "oauth"
        assert info.oauth_client == secrets
        assert info.terminal is False
        assert info.consent_skipped is False
        assert info.passed_over == ()

    def test_a_refresh(self, monkeypatch, tmp_path):
        creds = MagicMock(valid=False, expired=True, refresh_token="rt")
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        secrets = tmp_path / "gdrives_credentials.json"
        secrets.write_text("{}")
        (tmp_path / "gdrives_token.json").write_text("{}")
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        info = auth.describe_credentials()
        assert info.refresh is True
        assert info.oauth_client == secrets
        assert info.consent_skipped is False
        assert info.passed_over == ()

    def test_a_consent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(auth, "_is_interactive", lambda: True)
        secrets = tmp_path / "gdrives_credentials.json"
        secrets.write_text("{}")
        info = auth.describe_credentials()
        assert info.consent is True
        assert info.oauth_client == secrets
        assert info.terminal is True
        assert info.consent_skipped is False
        assert [p.reason for p in info.passed_over] == ["missing", "missing"]

    def test_a_service_account(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        secrets = tmp_path / "gdrives_credentials.json"
        secrets.write_text("{}")
        key = tmp_path / "service_account.json"
        key.write_text(json.dumps({"client_email": "sa@example.com"}))
        info = auth.describe_credentials()
        assert info.kind == "service_account"
        assert info.oauth_client == secrets
        assert info.terminal is False
        assert info.consent_skipped is True
        assert info.service_account == key
        assert [p.reason for p in info.passed_over] == ["missing", "missing"]

    def test_not_configured_versus_skipped_for_no_terminal(self, monkeypatch, tmp_path):
        not_configured = auth.describe_credentials()
        assert (not_configured.oauth_client, not_configured.consent_skipped) == (
            None,
            False,
        )
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        skipped = auth.describe_credentials()
        assert skipped.oauth_client == tmp_path / "gdrives_credentials.json"
        assert skipped.consent_skipped is True


class TestPassedOverTokens:
    """PassedToken and each of PASSED_REASONS, over the cached-token checks."""

    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        self.dir = tmp_path

    def test_missing(self):
        info = auth.describe_credentials()
        assert info.passed_over == (
            auth.PassedToken(path=self.dir / "gdrives_token.json", reason="missing"),
            auth.PassedToken(
                path=self.dir / "gdrives_token_drive-readonly.json",
                reason="missing",
            ),
        )

    def test_scopes(self):
        token = self.dir / "gdrives_token.json"
        token.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        info = auth.describe_credentials()
        assert info.passed_over[0] == auth.PassedToken(path=token, reason="scopes")

    def test_unreadable(self):
        token = self.dir / "gdrives_token.json"
        token.write_text("not json")
        info = auth.describe_credentials()
        assert info.passed_over[0] == auth.PassedToken(path=token, reason="unreadable")

    def test_invalid(self, monkeypatch):
        token = self.dir / "gdrives_token.json"
        token.write_text("{}")
        creds = MagicMock(valid=False, expired=False, refresh_token=None)
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        info = auth.describe_credentials()
        assert info.passed_over[0] == auth.PassedToken(path=token, reason="invalid")

    def test_a_scope_mismatch_beside_a_token_that_serves(self, monkeypatch):
        mismatched = self.dir / "gdrives_token.json"
        mismatched.write_text(json.dumps({"scopes": auth.SHEETS_WRITE_SCOPES}))
        derived = self.dir / "gdrives_token_drive-readonly.json"
        derived.write_text(json.dumps({"scopes": auth.SCOPES}))
        creds = MagicMock(valid=True, expired=False)
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        info = auth.describe_credentials()
        assert info.passed_over == (auth.PassedToken(path=mismatched, reason="scopes"),)
        assert info.source == derived


class TestNoSecretIsHeld:
    """No token, key, or secret value is ever held in a CredentialInfo field."""

    def test_no_sentinel_in_repr(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_PATH", raising=False)
        monkeypatch.setattr(auth, "_is_interactive", lambda: False)
        (tmp_path / "gdrives_credentials.json").write_text("{}")
        (tmp_path / "gdrives_token.json").write_text(
            json.dumps({"scopes": auth.SCOPES, "refresh_token": "SECRET-TOKEN-VALUE"})
        )
        key = tmp_path / "service_account.json"
        key.write_text(
            json.dumps(
                {
                    "client_email": "sa@example.com",
                    "private_key": "SECRET-KEY-VALUE",
                    "private_key_id": "SECRET-KEY-ID",
                }
            )
        )
        creds = MagicMock(valid=False, expired=False, refresh_token=None)
        monkeypatch.setattr(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            lambda path, scopes=None: creds,
        )
        info = auth.describe_credentials()
        assert info.kind == "service_account"
        assert [p.reason for p in info.passed_over] == ["invalid", "missing"]
        text = repr(info)
        for sentinel in ("SECRET-TOKEN-VALUE", "SECRET-KEY-VALUE", "SECRET-KEY-ID"):
            assert sentinel not in text
