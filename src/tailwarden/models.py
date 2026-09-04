from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_serializer,
    field_validator,
)

from .errors import UpstreamPayloadError


def _payload_error(message: str, payload: object) -> UpstreamPayloadError:
    return UpstreamPayloadError(service="Tailscale devices", message=message, payload=payload)


def _parse_datetime(value: object, *, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _payload_error(f"{field_name} must be an ISO-8601 string", value)

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _payload_error(f"{field_name} is not a valid ISO-8601 timestamp", value) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _first_payload_value(payload: Mapping[object, object], *names: str) -> object | None:
    for name in names:
        if name in payload:
            value = payload[name]
            if value is not None and value != "":
                return value
    return None


class TailwardenModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceReference(TailwardenModel):
    device_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)

    @classmethod
    def from_device(cls, device: Device) -> DeviceReference:
        return cls(device_id=device.id, name=device.name, target=device.target)


class Device(TailwardenModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    online: StrictBool | None = None
    key_expiry_disabled: StrictBool = False
    expires_at: datetime | None = None
    last_seen_at: datetime | None = None

    @field_validator("expires_at", "last_seen_at", mode="before")
    @classmethod
    def _reject_boolean_datetimes(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("datetime values cannot be booleans")
        return value

    @field_validator("expires_at", "last_seen_at", mode="after")
    @classmethod
    def _normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _normalize_datetime(value)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tag.strip() for tag in value):
            raise ValueError("tags must contain only non-empty strings")
        return tuple(tag.strip() for tag in value)

    @classmethod
    def from_tailscale_payload(cls, payload: object) -> Device:
        if not isinstance(payload, Mapping):
            raise _payload_error("device entry must be an object", payload)

        raw_id = payload.get("nodeId") or payload.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise _payload_error("device id is missing", payload)
        device_id = raw_id.strip()

        raw_name = payload.get("hostname") or payload.get("name") or device_id
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise _payload_error("device name is missing", payload)
        name = raw_name.strip()

        raw_addresses = payload.get("addresses")
        addresses: list[str] = []
        if raw_addresses not in (None, []):
            if not isinstance(raw_addresses, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_addresses
            ):
                raise _payload_error("addresses must be a list of non-empty strings", payload)
            addresses = [item.strip() for item in raw_addresses]

        raw_target = payload.get("dnsName") or (addresses[0] if addresses else None) or name
        if not isinstance(raw_target, str) or not raw_target.strip():
            raise _payload_error("device target is missing", payload)
        target = raw_target.strip()

        raw_tags = payload.get("tags")
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_tags
        ):
            raise _payload_error("tags must be a list of non-empty strings", payload)
        tags = tuple(item.strip() for item in raw_tags)

        raw_online = payload.get("online")
        if raw_online is None:
            online = None
        elif isinstance(raw_online, bool):
            online = raw_online
        else:
            raise _payload_error("online must be a boolean when present", payload)

        raw_key_expiry_disabled = payload.get("keyExpiryDisabled", False)
        if not isinstance(raw_key_expiry_disabled, bool):
            raise _payload_error("keyExpiryDisabled must be a boolean", payload)

        return cls(
            id=device_id,
            name=name,
            target=target,
            tags=tags,
            online=online,
            key_expiry_disabled=raw_key_expiry_disabled,
            expires_at=_parse_datetime(
                _first_payload_value(
                    payload,
                    "expires",
                    "keyExpiry",
                    "machineKeyExpiry",
                    "nodeKeyExpiry",
                    "expiry",
                ),
                field_name="expires",
            ),
            last_seen_at=_parse_datetime(payload.get("lastSeen"), field_name="lastSeen"),
        )

    def to_reference(self) -> DeviceReference:
        return DeviceReference.from_device(self)


class DeviceIssueCode(StrEnum):
    OFFLINE = "offline"
    STALE = "stale"
    UNKNOWN_LAST_SEEN = "unknown_last_seen"
    KEY_EXPIRING = "key_expiring"


class DeviceIssue(TailwardenModel):
    code: DeviceIssueCode
    device: DeviceReference
    detail: str
    observed_at: datetime

    @field_validator("observed_at", mode="before")
    @classmethod
    def _reject_boolean_observed_at(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("datetime values cannot be booleans")
        return value

    @field_validator("observed_at", mode="after")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class ActionCounts(TailwardenModel):
    planned: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    already: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class ActionError(TailwardenModel):
    action: str
    message: str
    status_code: int | None = None
    device: DeviceReference | None = None
    body: str | None = Field(default=None, exclude=True)


class KeepaliveOutcome(StrEnum):
    HEALTHY = "healthy"
    NO_MATCHES = "no_matches"
    STALE_DETECTED = "stale_detected"
    PARTIAL_FAILURE = "partial_failure"


class RecoveryKey(TailwardenModel):
    action: str = Field(min_length=1)
    key: SecretStr
    tag: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at", mode="before")
    @classmethod
    def _reject_boolean_created_at(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("datetime values cannot be booleans")
        return value

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_serializer("key", when_used="always")
    def _serialize_key(self, value: SecretStr) -> str:
        return "********"


class RecoveryKeyResponse(TailwardenModel):
    action: str = Field(min_length=1)
    key: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def _reject_boolean_created_at(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("datetime values cannot be booleans")
        return value

    @field_validator("created_at", mode="after")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @classmethod
    def from_recovery_key(cls, recovery_key: RecoveryKey) -> RecoveryKeyResponse:
        return cls(
            action=recovery_key.action,
            key=recovery_key.key.get_secret_value(),
            tag=recovery_key.tag,
            created_at=recovery_key.created_at,
        )


class KeepaliveReport(TailwardenModel):
    outcome: KeepaliveOutcome
    tailnet: str
    server_tag: str
    rejoin_auth_key_tag: str
    dry_run: bool = False
    fail_on_stale: bool = True
    stale_after_minutes: int
    key_expiry_warning_hours: int
    auth_key_expiry_seconds: int
    started_at: datetime
    finished_at: datetime
    total_devices: int = Field(default=0, ge=0)
    stale_devices: int = Field(default=0, ge=0)
    unknown_last_seen_devices: int = Field(default=0, ge=0)
    expiring_devices: int = Field(default=0, ge=0)
    key_expiry_actions: ActionCounts = Field(default_factory=ActionCounts)
    issues: list[DeviceIssue] = Field(default_factory=list)
    action_errors: list[ActionError] = Field(default_factory=list)

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _reject_boolean_run_times(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("datetime values cannot be booleans")
        return value

    @field_validator("started_at", "finished_at", mode="after")
    @classmethod
    def _normalize_run_times(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


__all__ = [
    "ActionCounts",
    "ActionError",
    "TailwardenModel",
    "Device",
    "DeviceIssue",
    "DeviceIssueCode",
    "DeviceReference",
    "KeepaliveOutcome",
    "KeepaliveReport",
    "RecoveryKey",
    "RecoveryKeyResponse",
]
