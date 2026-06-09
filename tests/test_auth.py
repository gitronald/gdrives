"""Tests for gdrives.auth — credential discovery and the fallback chain."""

import stat
from pathlib import Path
from unittest.mock import MagicMock

import google.auth.exceptions
import pytest

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
        monkeypatch.setattr(auth, "authenticate_oauth", lambda: sentinel)
        monkeypatch.setattr(
            auth,
            "authenticate_service_account",
            lambda: pytest.fail("must not reach service account"),
        )
        assert auth.authenticate() is sentinel

    def test_falls_back_to_service_account(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(auth, "authenticate_oauth", lambda: None)
        monkeypatch.setattr(auth, "authenticate_service_account", lambda: sentinel)
        monkeypatch.setattr(
            auth, "authenticate_adc", lambda: pytest.fail("must not reach adc")
        )
        assert auth.authenticate() is sentinel

    def test_falls_through_to_adc(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(auth, "authenticate_oauth", lambda: None)
        monkeypatch.setattr(auth, "authenticate_service_account", lambda: None)
        monkeypatch.setattr(auth, "authenticate_adc", lambda: sentinel)
        assert auth.authenticate() is sentinel

    def test_helpful_error_when_no_credentials(self, monkeypatch):
        monkeypatch.setattr(auth, "authenticate_oauth", lambda: None)
        monkeypatch.setattr(auth, "authenticate_service_account", lambda: None)

        def raise_default_error():
            raise google.auth.exceptions.DefaultCredentialsError("none found")

        monkeypatch.setattr(auth, "authenticate_adc", raise_default_error)
        with pytest.raises(SystemExit, match="no Google Drive credentials found"):
            auth.authenticate()

    def test_helpful_error_on_any_google_auth_error_from_adc(self, monkeypatch):
        # Not only DefaultCredentialsError — any google.auth error (e.g. a stale
        # ADC refresh failure) yields the helpful message, not a raw traceback.
        monkeypatch.setattr(auth, "authenticate_oauth", lambda: None)
        monkeypatch.setattr(auth, "authenticate_service_account", lambda: None)

        def raise_refresh_error():
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


# -- build_drive_service --


class TestBuildDriveService:
    def test_builds_v3_with_authenticated_creds(self, monkeypatch):
        creds = object()
        service = object()
        monkeypatch.setattr(auth, "authenticate", lambda: creds)
        rec = {}
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda *a, **k: rec.update(a=a, k=k) or service,
        )
        assert auth.build_drive_service() is service
        assert rec["a"] == ("drive", "v3")
        assert rec["k"]["credentials"] is creds
