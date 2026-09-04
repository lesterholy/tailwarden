from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .config import Settings
from .errors import UpstreamError, UpstreamHTTPError
from .models import (
    ActionCounts,
    ActionError,
    Device,
    DeviceIssue,
    DeviceIssueCode,
    KeepaliveOutcome,
    KeepaliveReport,
    RecoveryKey,
)


class KeepaliveClient(Protocol):
    async def list_devices(self, tailnet: str) -> list[Device]: ...

    async def disable_key_expiry(self, device_id: str) -> None: ...

    async def create_recovery_key(
        self,
        tailnet: str,
        tag: str,
        expiry_seconds: int,
        description: str,
    ) -> RecoveryKey: ...


class KeepaliveService:
    def __init__(
        self,
        settings: Settings,
        client: KeepaliveClient,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _matches_selector(self, device: Device) -> bool:
        selector = self._settings.server_tag
        if selector in {"all", "*"}:
            return True
        if selector == "tag:*":
            return any(tag.startswith("tag:") for tag in device.tags)
        return selector in device.tags

    async def run_keepalive(
        self,
        *,
        dry_run: bool | None = None,
        fail_on_stale: bool | None = None,
    ) -> KeepaliveReport:
        if dry_run is not None and not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a boolean")
        if fail_on_stale is not None and not isinstance(fail_on_stale, bool):
            raise TypeError("fail_on_stale must be a boolean")

        started_at = self._now()
        effective_dry_run = self._settings.dry_run if dry_run is None else dry_run
        effective_fail_on_stale = (
            self._settings.fail_on_stale if fail_on_stale is None else fail_on_stale
        )
        report = KeepaliveReport(
            outcome=KeepaliveOutcome.HEALTHY,
            tailnet=self._settings.tailnet,
            server_tag=self._settings.server_tag,
            rejoin_auth_key_tag=self._settings.rejoin_auth_key_tag,
            dry_run=effective_dry_run,
            fail_on_stale=effective_fail_on_stale,
            stale_after_minutes=self._settings.stale_after_minutes,
            key_expiry_warning_hours=self._settings.key_expiry_warning_hours,
            auth_key_expiry_seconds=self._settings.auth_key_expiry_seconds,
            started_at=started_at,
            finished_at=started_at,
            key_expiry_actions=ActionCounts(),
        )

        all_devices = await self._client.list_devices(self._settings.tailnet)
        devices = [device for device in all_devices if self._matches_selector(device)]
        report.total_devices = len(devices)

        stale_cutoff = started_at - timedelta(minutes=self._settings.stale_after_minutes)
        expiry_cutoff = started_at + timedelta(hours=self._settings.key_expiry_warning_hours)

        for device in devices:
            self._record_device_issues(
                report,
                device,
                stale_cutoff=stale_cutoff,
                expiry_cutoff=expiry_cutoff,
                now=started_at,
            )
            await self._maintain_key_expiry(report, device)

        report.finished_at = self._now()
        report.outcome = self._resolve_outcome(report)
        return report

    def _record_device_issues(
        self,
        report: KeepaliveReport,
        device: Device,
        *,
        stale_cutoff: datetime,
        expiry_cutoff: datetime,
        now: datetime,
    ) -> None:
        reference = device.to_reference()
        if device.online is False:
            report.stale_devices += 1
            report.issues.append(
                DeviceIssue(
                    code=DeviceIssueCode.OFFLINE,
                    device=reference,
                    detail="Tailscale reports online=false",
                    observed_at=device.last_seen_at or now,
                )
            )
        elif device.last_seen_at is None:
            report.unknown_last_seen_devices += 1
            report.issues.append(
                DeviceIssue(
                    code=DeviceIssueCode.UNKNOWN_LAST_SEEN,
                    device=reference,
                    detail="lastSeen is unavailable",
                    observed_at=now,
                )
            )
        elif device.last_seen_at < stale_cutoff:
            report.stale_devices += 1
            report.issues.append(
                DeviceIssue(
                    code=DeviceIssueCode.STALE,
                    device=reference,
                    detail=f"last seen at {device.last_seen_at.isoformat()}",
                    observed_at=device.last_seen_at,
                )
            )

        if (
            not device.key_expiry_disabled
            and device.expires_at is not None
            and device.expires_at <= expiry_cutoff
        ):
            report.expiring_devices += 1
            report.issues.append(
                DeviceIssue(
                    code=DeviceIssueCode.KEY_EXPIRING,
                    device=reference,
                    detail=f"key expires at {device.expires_at.isoformat()}",
                    observed_at=device.expires_at,
                )
            )

    async def _maintain_key_expiry(
        self,
        report: KeepaliveReport,
        device: Device,
    ) -> None:
        if device.key_expiry_disabled:
            report.key_expiry_actions.already += 1
            return
        if report.dry_run:
            report.key_expiry_actions.planned += 1
            return

        try:
            await self._client.disable_key_expiry(device.id)
        except UpstreamError as exc:
            report.key_expiry_actions.failed += 1
            report.action_errors.append(
                ActionError(
                    action="disable-key-expiry",
                    message=exc.message if isinstance(exc, UpstreamHTTPError) else str(exc),
                    status_code=exc.status_code if isinstance(exc, UpstreamHTTPError) else None,
                    device=device.to_reference(),
                    body=exc.body if isinstance(exc, UpstreamHTTPError) else None,
                )
            )
        else:
            report.key_expiry_actions.succeeded += 1

    @staticmethod
    def _resolve_outcome(report: KeepaliveReport) -> KeepaliveOutcome:
        if report.action_errors:
            return KeepaliveOutcome.PARTIAL_FAILURE
        if report.total_devices == 0:
            return KeepaliveOutcome.NO_MATCHES
        if report.stale_devices:
            return KeepaliveOutcome.STALE_DETECTED
        return KeepaliveOutcome.HEALTHY

    async def create_recovery_key(self) -> RecoveryKey:
        return await self._client.create_recovery_key(
            self._settings.tailnet,
            self._settings.rejoin_auth_key_tag,
            self._settings.auth_key_expiry_seconds,
            self._settings.auth_key_description,
        )


__all__ = ["KeepaliveClient", "KeepaliveService"]
