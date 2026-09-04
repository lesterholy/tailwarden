from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tailwarden.config import Settings


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_HOST": "127.0.0.1",
        "APP_PORT": 8000,
        "APP_LOG_LEVEL": "INFO",
        "TAILNET": "-",
        "SERVER_TAG": "tag:server",
        "REJOIN_AUTH_KEY_TAG": "tag:server",
        "STALE_AFTER_MINUTES": 30,
        "KEY_EXPIRY_WARNING_HOURS": 168,
        "AUTH_KEY_EXPIRY_SECONDS": 3600,
        "AUTH_KEY_DESCRIPTION": "test description",
        "DRY_RUN": False,
        "FAIL_ON_STALE": True,
        "HTTP_TOTAL_TIMEOUT_SECONDS": 20,
        "HTTP_CONNECT_TIMEOUT_SECONDS": 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_default_tags_use_dedicated_managed_tag() -> None:
    settings = Settings(_env_file=None)

    assert settings.server_tag == "tag:tailwarden-managed"
    assert settings.rejoin_auth_key_tag == "tag:tailwarden-managed"


@pytest.mark.parametrize("value", ["all", "*", "tag:*", "tag:server"])
def test_server_tag_accepts_supported_selectors(value: str) -> None:
    assert build_settings(SERVER_TAG=value).server_tag == value


@pytest.mark.parametrize("value", ["all", "*", "tag:*"])
def test_github_actions_requires_concrete_server_tag(value: str) -> None:
    with pytest.raises(
        ValidationError,
        match="SERVER_TAG must be a concrete tag:<name> in GitHub Actions",
    ):
        build_settings(GITHUB_ACTIONS=True, SERVER_TAG=value)


@pytest.mark.parametrize("value", ["", "all", "*", "tag:*", "server", "tag:bad tag"])
def test_rejoin_auth_key_tag_requires_a_concrete_tag(value: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(REJOIN_AUTH_KEY_TAG=value)


@pytest.mark.parametrize(
    "field",
    ["STALE_AFTER_MINUTES", "KEY_EXPIRY_WARNING_HOURS", "AUTH_KEY_EXPIRY_SECONDS"],
)
@pytest.mark.parametrize("value", [0, -1, False, 1.5, "1.5", "invalid"])
def test_positive_integer_settings_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        build_settings(**{field: value})


def test_positive_integer_settings_accept_decimal_strings() -> None:
    settings = build_settings(
        STALE_AFTER_MINUTES="30",
        KEY_EXPIRY_WARNING_HOURS="168",
        AUTH_KEY_EXPIRY_SECONDS="3600",
    )
    assert settings.stale_after_minutes == 30
    assert settings.key_expiry_warning_hours == 168
    assert settings.auth_key_expiry_seconds == 3600


def test_empty_optional_values_become_none() -> None:
    settings = build_settings(
        APP_API_TOKEN="   ",
        TS_TOKEN="",
        TS_CLIENT_ID=" ",
        TS_AUDIENCE="",
        ACTIONS_ID_TOKEN_REQUEST_TOKEN="  ",
        ACTIONS_ID_TOKEN_REQUEST_URL=" ",
    )
    assert settings.app_api_token is None
    assert settings.ts_token is None
    assert settings.ts_client_id is None
    assert settings.ts_audience is None
    assert settings.actions_id_token_request_token is None
    assert settings.actions_id_token_request_url is None


def test_process_environment_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "TAILNET=dotenv.example\nSERVER_TAG=tag:dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERVER_TAG", "tag:environment")

    settings = Settings()

    assert settings.tailnet == "dotenv.example"
    assert settings.server_tag == "tag:environment"


@pytest.mark.parametrize("value", ["tskey-api-secret", "https://login.tailscale.com/CNTRL"])
def test_tailnet_rejects_credential_or_control_url_values(value: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(TAILNET=value)


@pytest.mark.parametrize(
    "value",
    ["client-id", "https://api.tailscale.com/client-id", "api.tailscale.com/"],
)
def test_oidc_audience_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(TS_AUDIENCE=value)


def test_oidc_request_url_requires_https_except_for_loopback() -> None:
    assert (
        build_settings(
            ACTIONS_ID_TOKEN_REQUEST_URL="http://127.0.0.1:8123/oidc"
        ).actions_id_token_request_url
        == "http://127.0.0.1:8123/oidc"
    )
    with pytest.raises(ValidationError):
        build_settings(ACTIONS_ID_TOKEN_REQUEST_URL="http://example.com/oidc")


def test_app_port_and_http_timeouts_are_strictly_positive() -> None:
    with pytest.raises(ValidationError):
        build_settings(APP_PORT=True)
    with pytest.raises(ValidationError):
        build_settings(HTTP_TOTAL_TIMEOUT_SECONDS=0)
    with pytest.raises(ValidationError):
        build_settings(HTTP_CONNECT_TIMEOUT_SECONDS="nan")
