from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tailwarden.config import Settings
from tailwarden.errors import UpstreamError, UpstreamHTTPError, UpstreamPayloadError
from tailwarden.models import Device, DeviceIssueCode, KeepaliveOutcome, RecoveryKey
from tailwarden.service import KeepaliveService

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TAILNET": "-",
        "SERVER_TAG": "tag:server",
        "REJOIN_AUTH_KEY_TAG": "tag:server",
        "AUTH_KEY_DESCRIPTION": "controlled recovery",
        "STALE_AFTER_MINUTES": 30,
        "KEY_EXPIRY_WARNING_HOURS": 168,
        "AUTH_KEY_EXPIRY_SECONDS": 3600,
        "FAIL_ON_STALE": True,
        "DRY_RUN": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeClient:
    def __init__(
        self,
        devices: list[Device],
        failures: dict[str, UpstreamError] | None = None,
    ) -> None:
        self.devices = devices
        self.failures = failures or {}
        self.listed_tailnets: list[str] = []
        self.disabled_ids: list[str] = []
        self.recovery_requests: list[tuple[str, str, int, str]] = []

    async def list_devices(self, tailnet: str) -> list[Device]:
        self.listed_tailnets.append(tailnet)
        return self.devices

    async def disable_key_expiry(self, device_id: str) -> None:
        if failure := self.failures.get(device_id):
            raise failure
        self.disabled_ids.append(device_id)

    async def create_recovery_key(
        self,
        tailnet: str,
        tag: str,
        expiry_seconds: int,
        description: str,
    ) -> RecoveryKey:
        self.recovery_requests.append((tailnet, tag, expiry_seconds, description))
        return RecoveryKey(action="created", key="tskey-auth-recovery", tag=tag)


def make_device(
    device_id: str,
    *,
    tags: tuple[str, ...] = ("tag:server",),
    online: bool | None = True,
    key_expiry_disabled: bool = True,
    expires_at: datetime | None = None,
    last_seen_at: datetime | None = NOW,
) -> Device:
    return Device(
        id=device_id,
        name=device_id,
        target=f"{device_id}.tail",
        tags=tags,
        online=online,
        key_expiry_disabled=key_expiry_disabled,
        expires_at=expires_at,
        last_seen_at=last_seen_at,
    )


def make_service(
    client: FakeClient,
    **settings_overrides: object,
) -> KeepaliveService:
    return KeepaliveService(
        build_settings(**settings_overrides),
        client,
        now_factory=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_dry_run_only_plans_actions_and_counts_existing_state() -> None:
    client = FakeClient(
        [
            make_device(
                "needs-change",
                key_expiry_disabled=False,
                expires_at=NOW + timedelta(hours=1),
            ),
            make_device("already-disabled"),
        ]
    )

    report = await make_service(client, DRY_RUN=True).run_keepalive()

    assert report.outcome is KeepaliveOutcome.HEALTHY
    assert report.key_expiry_actions.planned == 1
    assert report.key_expiry_actions.succeeded == 0
    assert report.key_expiry_actions.failed == 0
    assert report.key_expiry_actions.already == 1
    assert report.expiring_devices == 1
    assert client.disabled_ids == []


@pytest.mark.asyncio
async def test_apply_counts_only_real_success_and_continues_after_failures() -> None:
    failures = {
        "forbidden": UpstreamHTTPError(
            service="Tailscale",
            status_code=403,
            message="forbidden",
            body='{"message":"forbidden"}',
        ),
        "transport": UpstreamError("Tailscale transport error"),
    }
    client = FakeClient(
        [
            make_device("success", key_expiry_disabled=False),
            make_device("forbidden", key_expiry_disabled=False),
            make_device("transport", key_expiry_disabled=False),
        ],
        failures,
    )

    report = await make_service(client).run_keepalive()

    assert report.outcome is KeepaliveOutcome.PARTIAL_FAILURE
    assert report.key_expiry_actions.planned == 0
    assert report.key_expiry_actions.succeeded == 1
    assert report.key_expiry_actions.failed == 2
    assert client.disabled_ids == ["success"]
    assert [error.status_code for error in report.action_errors] == [403, None]
    assert "body" not in report.model_dump()["action_errors"][0]


@pytest.mark.asyncio
async def test_stale_rules_are_mutually_exclusive_and_cutoff_is_strict() -> None:
    cutoff = NOW - timedelta(minutes=30)
    client = FakeClient(
        [
            make_device("offline", online=False, last_seen_at=None),
            make_device("stale", last_seen_at=cutoff - timedelta(microseconds=1)),
            make_device("at-cutoff", last_seen_at=cutoff),
            make_device("unknown", last_seen_at=None),
            make_device("fresh", last_seen_at=NOW),
        ]
    )

    report = await make_service(client).run_keepalive()

    assert report.outcome is KeepaliveOutcome.STALE_DETECTED
    assert report.stale_devices == 2
    assert report.unknown_last_seen_devices == 1
    codes = [issue.code for issue in report.issues]
    assert codes.count(DeviceIssueCode.OFFLINE) == 1
    assert codes.count(DeviceIssueCode.STALE) == 1
    assert codes.count(DeviceIssueCode.UNKNOWN_LAST_SEEN) == 1


@pytest.mark.asyncio
async def test_fail_on_stale_changes_exit_policy_not_report_outcome() -> None:
    client = FakeClient([make_device("offline", online=False)])

    report = await make_service(client).run_keepalive(fail_on_stale=False)

    assert report.outcome is KeepaliveOutcome.STALE_DETECTED
    assert report.fail_on_stale is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("all", {"server", "web", "untagged"}),
        ("*", {"server", "web", "untagged"}),
        ("tag:*", {"server", "web"}),
        ("tag:web", {"web"}),
    ],
)
async def test_device_selector_semantics(selector: str, expected: set[str]) -> None:
    client = FakeClient(
        [
            make_device("server", tags=("tag:server",), key_expiry_disabled=False),
            make_device("web", tags=("tag:web",), key_expiry_disabled=False),
            make_device("untagged", tags=(), key_expiry_disabled=False),
        ]
    )

    report = await make_service(client, SERVER_TAG=selector).run_keepalive()

    assert report.total_devices == len(expected)
    assert set(client.disabled_ids) == expected
    assert client.listed_tailnets == ["-"]


@pytest.mark.asyncio
async def test_no_matches_is_a_distinct_successful_report() -> None:
    client = FakeClient([make_device("server", tags=("tag:server",))])

    report = await make_service(client, SERVER_TAG="tag:db").run_keepalive()

    assert report.outcome is KeepaliveOutcome.NO_MATCHES
    assert report.total_devices == 0


@pytest.mark.asyncio
async def test_normal_keepalive_never_creates_a_recovery_key() -> None:
    client = FakeClient([make_device("offline", online=False)])

    await make_service(client).run_keepalive()

    assert client.recovery_requests == []


@pytest.mark.asyncio
async def test_recovery_key_creation_is_explicit_and_uses_configured_tag() -> None:
    client = FakeClient([])
    service = make_service(
        client,
        REJOIN_AUTH_KEY_TAG="tag:web",
        AUTH_KEY_EXPIRY_SECONDS=900,
        AUTH_KEY_DESCRIPTION="restore web node",
    )

    recovery = await service.create_recovery_key()

    assert recovery.key.get_secret_value() == "tskey-auth-recovery"
    assert client.recovery_requests == [("-", "tag:web", 900, "restore web node")]


@pytest.mark.asyncio
async def test_list_payload_errors_are_not_misreported_as_no_matches() -> None:
    class BrokenClient(FakeClient):
        async def list_devices(self, tailnet: str) -> list[Device]:
            raise UpstreamPayloadError(
                service="Tailscale devices",
                message="invalid devices payload",
            )

    with pytest.raises(UpstreamPayloadError):
        await make_service(BrokenClient([])).run_keepalive()
