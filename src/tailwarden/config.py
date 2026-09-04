from __future__ import annotations

import re
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigurationError

_TAG_PATTERN = re.compile(r"tag:[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_INTEGER_PATTERN = re.compile(r"[0-9]+\Z")
_NUMBER_PATTERN = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)\Z")
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        populate_by_name=True,
        validate_default=True,
    )

    tailnet: str = Field(default="-", alias="TAILNET")
    server_tag: str = Field(default="tag:tailwarden-managed", alias="SERVER_TAG")
    rejoin_auth_key_tag: str = Field(default="tag:tailwarden-managed", alias="REJOIN_AUTH_KEY_TAG")
    stale_after_minutes: int = Field(default=30, alias="STALE_AFTER_MINUTES", gt=0)
    key_expiry_warning_hours: int = Field(
        default=168,
        alias="KEY_EXPIRY_WARNING_HOURS",
        gt=0,
    )
    auth_key_expiry_seconds: int = Field(
        default=3600,
        alias="AUTH_KEY_EXPIRY_SECONDS",
        gt=0,
    )
    auth_key_description: str = Field(
        default="emergency server rejoin from tailwarden",
        alias="AUTH_KEY_DESCRIPTION",
        min_length=1,
    )
    dry_run: bool = Field(default=False, alias="DRY_RUN")
    fail_on_stale: bool = Field(default=True, alias="FAIL_ON_STALE")
    report_redact_details: bool = Field(default=False, alias="REPORT_REDACT_DETAILS")

    app_host: str = Field(default="127.0.0.1", alias="APP_HOST", min_length=1)
    app_port: int = Field(default=8000, alias="APP_PORT", ge=1, le=65535)
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    app_api_token: SecretStr | None = Field(default=None, alias="APP_API_TOKEN", repr=False)

    ts_token: SecretStr | None = Field(default=None, alias="TS_TOKEN", repr=False)
    ts_client_id: str | None = Field(default=None, alias="TS_CLIENT_ID")
    ts_audience: str | None = Field(default=None, alias="TS_AUDIENCE")
    actions_id_token_request_token: SecretStr | None = Field(
        default=None,
        alias="ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        repr=False,
    )
    actions_id_token_request_url: str | None = Field(
        default=None,
        alias="ACTIONS_ID_TOKEN_REQUEST_URL",
    )

    github_step_summary: str | None = Field(default=None, alias="GITHUB_STEP_SUMMARY")
    github_actions: bool = Field(default=False, alias="GITHUB_ACTIONS")

    http_total_timeout_seconds: float = Field(
        default=20.0,
        alias="HTTP_TOTAL_TIMEOUT_SECONDS",
        gt=0,
        allow_inf_nan=False,
    )
    http_connect_timeout_seconds: float = Field(
        default=5.0,
        alias="HTTP_CONNECT_TIMEOUT_SECONDS",
        gt=0,
        allow_inf_nan=False,
    )

    @field_validator(
        "stale_after_minutes",
        "key_expiry_warning_hours",
        "auth_key_expiry_seconds",
        mode="before",
    )
    @classmethod
    def _parse_positive_integer(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("must be a positive integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value.strip()):
            return int(value.strip(), 10)
        raise ValueError("must be a positive integer")

    @field_validator("app_port", mode="before")
    @classmethod
    def _parse_port(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("APP_PORT must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value.strip()):
            return int(value.strip(), 10)
        raise ValueError("APP_PORT must be an integer")

    @field_validator(
        "http_total_timeout_seconds",
        "http_connect_timeout_seconds",
        mode="before",
    )
    @classmethod
    def _parse_positive_number(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("must be a positive number")
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str) and _NUMBER_PATTERN.fullmatch(value.strip()):
            return float(value.strip())
        raise ValueError("must be a positive number")

    @field_validator("tailnet")
    @classmethod
    def _validate_tailnet(cls, value: str) -> str:
        normalized = value.strip() or "-"
        if normalized.startswith("tskey-") or normalized.endswith("CNTRL"):
            raise ValueError("TAILNET must be a tailnet name, not a credential or control URL")
        if any(character.isspace() for character in normalized) or any(
            character in normalized for character in "/\\?#"
        ):
            raise ValueError("TAILNET contains unsupported characters")
        return normalized

    @field_validator("server_tag")
    @classmethod
    def _validate_server_tag(cls, value: str) -> str:
        normalized = value.strip()
        if normalized in {"all", "*", "tag:*"}:
            return normalized
        if not _TAG_PATTERN.fullmatch(normalized):
            raise ValueError("SERVER_TAG must be all, *, tag:*, or a tag:<name>")
        return normalized

    @field_validator("rejoin_auth_key_tag")
    @classmethod
    def _validate_rejoin_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not _TAG_PATTERN.fullmatch(normalized):
            raise ValueError("REJOIN_AUTH_KEY_TAG must be a concrete tag:<name>")
        return normalized

    @field_validator("auth_key_description", "app_host", mode="before")
    @classmethod
    def _normalize_required_strings(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @field_validator("app_log_level", mode="before")
    @classmethod
    def _validate_log_level(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("APP_LOG_LEVEL must be a string")
        normalized = value.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError("APP_LOG_LEVEL is not supported")
        return normalized

    @field_validator("ts_client_id", "github_step_summary", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("actions_id_token_request_url", mode="before")
    @classmethod
    def _validate_oidc_request_url(cls, value: object | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalized = str(value).strip()
        parsed = urlsplit(normalized)
        loopback_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
        if not parsed.netloc or (parsed.scheme != "https" and not loopback_http):
            raise ValueError(
                "ACTIONS_ID_TOKEN_REQUEST_URL must use HTTPS (HTTP is allowed on loopback)"
            )
        return normalized

    @field_validator("ts_token", "app_api_token", "actions_id_token_request_token", mode="before")
    @classmethod
    def _normalize_optional_secrets(
        cls,
        value: SecretStr | str | None,
    ) -> SecretStr | None:
        if value is None:
            return None
        normalized = (
            value.get_secret_value().strip() if isinstance(value, SecretStr) else str(value).strip()
        )
        return SecretStr(normalized) if normalized else None

    @field_validator("ts_audience", mode="before")
    @classmethod
    def _validate_audience(cls, value: object | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        normalized = str(value).strip()
        prefix = "api.tailscale.com/"
        if not normalized.startswith(prefix) or not normalized.removeprefix(prefix):
            raise ValueError("TS_AUDIENCE must look like api.tailscale.com/<client-id>")
        if any(character.isspace() for character in normalized):
            raise ValueError("TS_AUDIENCE cannot contain whitespace")
        return normalized

    @model_validator(mode="after")
    def _require_concrete_github_actions_tag(self) -> Self:
        if self.github_actions and self.server_tag in {"all", "*", "tag:*"}:
            raise ValueError("SERVER_TAG must be a concrete tag:<name> in GitHub Actions")
        return self

    @property
    def has_static_tailscale_token(self) -> bool:
        return self.ts_token is not None

    @property
    def has_oidc_exchange_config(self) -> bool:
        return all(
            (
                self.ts_client_id,
                self.ts_audience,
                self.actions_id_token_request_token,
                self.actions_id_token_request_url,
            )
        )

    def require_app_api_token(self) -> SecretStr:
        if self.app_api_token is None:
            raise ConfigurationError("APP_API_TOKEN is required for this operation")
        return self.app_api_token

    def require_tailscale_token(self) -> SecretStr:
        if self.ts_token is None:
            raise ConfigurationError("TS_TOKEN is not configured")
        return self.ts_token

    def require_oidc_exchange(self) -> Self:
        if not self.has_oidc_exchange_config:
            raise ConfigurationError(
                "TS_CLIENT_ID, TS_AUDIENCE, ACTIONS_ID_TOKEN_REQUEST_TOKEN, and "
                "ACTIONS_ID_TOKEN_REQUEST_URL are required for OIDC exchange"
            )
        return self


__all__ = ["Settings"]
