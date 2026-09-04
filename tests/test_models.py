from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tailwarden.errors import UpstreamPayloadError
from tailwarden.models import (
    ActionError,
    Device,
    KeepaliveOutcome,
    KeepaliveReport,
    RecoveryKey,
    RecoveryKeyResponse,
)


def test_device_normalizes_tailscale_fields_and_timestamps() -> None:
    device = Device.from_tailscale_payload(
        {
            "nodeId": " node-123 ",
            "id": "fallback-id",
            "hostname": "server-1",
            "dnsName": "server-1.example.ts.net",
            "addresses": ["100.64.0.10"],
            "tags": ["tag:server", "tag:blue"],
            "online": False,
            "keyExpiryDisabled": False,
            "expires": "2026-09-02T00:00:00.123Z",
            "lastSeen": "2026-09-02T01:02:03.456Z",
        }
    )

    assert device.id == "node-123"
    assert device.name == "server-1"
    assert device.target == "server-1.example.ts.net"
    assert device.tags == ("tag:server", "tag:blue")
    assert device.online is False
    assert device.expires_at == datetime(2026, 9, 2, 0, 0, 0, 123000, tzinfo=UTC)
    assert device.last_seen_at == datetime(2026, 9, 2, 1, 2, 3, 456000, tzinfo=UTC)


def test_device_falls_back_to_id_name_and_address() -> None:
    device = Device.from_tailscale_payload(
        {
            "id": "legacy-id",
            "name": "raw-name",
            "addresses": ["100.64.0.10"],
        }
    )
    assert device.id == "legacy-id"
    assert device.name == "raw-name"
    assert device.target == "100.64.0.10"
    assert device.key_expiry_disabled is False


def test_device_uses_device_id_when_tailscale_name_fields_are_missing() -> None:
    device = Device.from_tailscale_payload(
        {
            "id": "legacy-id",
            "addresses": ["100.64.0.10"],
        }
    )

    assert device.name == "legacy-id"
    assert device.target == "100.64.0.10"


def test_device_uses_first_non_empty_expiry_fallback() -> None:
    device = Device.from_tailscale_payload(
        {
            "id": "node",
            "expires": None,
            "keyExpiry": "2026-09-03T00:00:00Z",
        }
    )
    assert device.expires_at == datetime(2026, 9, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "missing-id"},
        {"id": "node", "online": "false"},
        {"id": "node", "keyExpiryDisabled": "false"},
        {"id": "node", "tags": "tag:server"},
        {"id": "node", "tags": [""]},
        {"id": "node", "addresses": [100]},
        {"id": "node", "lastSeen": False},
        {"id": "node", "expires": "not-a-time"},
        "not-an-object",
    ],
)
def test_device_rejects_malformed_payload(payload: object) -> None:
    with pytest.raises(UpstreamPayloadError):
        Device.from_tailscale_payload(payload)


def test_device_direct_model_rejects_string_booleans() -> None:
    with pytest.raises(ValidationError):
        Device(
            id="node",
            name="node",
            target="node.tail",
            online="false",
        )


def test_device_direct_model_normalizes_naive_datetimes_to_utc() -> None:
    device = Device(
        id="node",
        name="node",
        target="node.tail",
        last_seen_at=datetime(2026, 9, 2, 12, 0),
    )
    assert device.last_seen_at == datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_action_error_body_is_excluded_even_when_nested() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    error = ActionError(
        action="disable-key-expiry",
        message="forbidden",
        status_code=403,
        body='{"secret":"hidden"}',
    )
    report = KeepaliveReport(
        outcome=KeepaliveOutcome.PARTIAL_FAILURE,
        tailnet="-",
        server_tag="tag:server",
        rejoin_auth_key_tag="tag:server",
        stale_after_minutes=30,
        key_expiry_warning_hours=168,
        auth_key_expiry_seconds=3600,
        started_at=now,
        finished_at=now,
        action_errors=[error],
    )

    assert "body" not in error.model_dump()
    assert "body" not in report.model_dump()["action_errors"][0]


def test_recovery_key_is_masked_except_for_explicit_response_model() -> None:
    recovery_key = RecoveryKey(
        action="created",
        key="tskey-auth-k123",
        tag="tag:server",
    )

    assert recovery_key.model_dump()["key"] == "********"
    assert json.loads(recovery_key.model_dump_json())["key"] == "********"
    assert RecoveryKeyResponse.from_recovery_key(recovery_key).key == "tskey-auth-k123"
