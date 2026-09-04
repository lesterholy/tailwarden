from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

import tailwarden.runtime as runtime_module
from tailwarden.config import Settings
from tailwarden.errors import RunInProgressError, UpstreamError
from tailwarden.models import KeepaliveOutcome, KeepaliveReport, RecoveryKey
from tailwarden.runtime import AppRuntime


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"TS_TOKEN": "static-token"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def sample_report() -> KeepaliveReport:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    return KeepaliveReport(
        outcome=KeepaliveOutcome.HEALTHY,
        tailnet="-",
        server_tag="tag:server",
        rejoin_auth_key_tag="tag:server",
        stale_after_minutes=30,
        key_expiry_warning_hours=168,
        auth_key_expiry_seconds=3600,
        started_at=now,
        finished_at=now,
    )


@pytest.mark.asyncio
async def test_ready_requires_a_live_session_and_credentials() -> None:
    configured = AppRuntime(build_settings())
    assert configured.ready is False
    await configured.start()
    assert configured.ready is True
    await configured.close()
    assert configured.ready is False

    unconfigured = AppRuntime(build_settings(TS_TOKEN=""))
    await unconfigured.start()
    assert unconfigured.ready is False
    await unconfigured.close()

    github_with_static_token = AppRuntime(build_settings(GITHUB_ACTIONS=True))
    await github_with_static_token.start()
    assert github_with_static_token.ready is False
    await github_with_static_token.close()


@pytest.mark.asyncio
async def test_each_operation_resolves_one_token_and_reuses_runtime_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_sessions: list[object] = []
    client_sessions: list[object] = []

    async def resolve(settings: Settings, session: object) -> SecretStr:
        resolver_sessions.append(session)
        return SecretStr("resolved-token")

    class FakeClient:
        def __init__(self, session: object, token: SecretStr, **_: object) -> None:
            assert token.get_secret_value() == "resolved-token"
            client_sessions.append(session)

        async def list_devices(self, tailnet: str) -> list[object]:
            assert tailnet == "-"
            return []

        async def disable_key_expiry(self, device_id: str) -> None:
            raise AssertionError(device_id)

        async def create_recovery_key(
            self,
            tailnet: str,
            tag: str,
            expiry_seconds: int,
            description: str,
        ) -> RecoveryKey:
            assert (tailnet, tag, expiry_seconds) == ("-", "tag:tailwarden-managed", 3600)
            assert description
            return RecoveryKey(action="created", key="tskey-auth-value", tag=tag)

    monkeypatch.setattr(runtime_module, "resolve_tailscale_token", resolve)
    monkeypatch.setattr(runtime_module, "TailscaleClient", FakeClient)
    runtime = AppRuntime(build_settings())
    await runtime.start()
    try:
        report = await runtime.run_keepalive()
        recovery = await runtime.create_recovery_key()
    finally:
        await runtime.close()

    assert report.outcome is KeepaliveOutcome.NO_MATCHES
    assert recovery.key.get_secret_value() == "tskey-auth-value"
    assert len(resolver_sessions) == 2
    assert resolver_sessions[0] is resolver_sessions[1]
    assert client_sessions == resolver_sessions
    assert runtime.last_report is report


@pytest.mark.asyncio
async def test_keepalive_and_recovery_operations_conflict_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingService:
        async def run_keepalive(self, **_: object) -> KeepaliveReport:
            entered.set()
            await release.wait()
            return sample_report()

        async def create_recovery_key(self) -> RecoveryKey:
            raise AssertionError("recovery must not start while keepalive is running")

    runtime = AppRuntime(build_settings())

    async def build_service() -> BlockingService:
        return BlockingService()

    monkeypatch.setattr(runtime, "_build_service", build_service)
    await runtime.start()
    task = asyncio.create_task(runtime.run_keepalive())
    await entered.wait()
    try:
        with pytest.raises(RunInProgressError) as caught:
            await runtime.create_recovery_key()
        assert caught.value.owner == "keepalive"
        assert runtime.run_in_progress is True
    finally:
        release.set()
        await task
        await runtime.close()

    assert runtime.run_in_progress is False


@pytest.mark.asyncio
async def test_failed_operation_releases_lock_and_does_not_replace_last_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = sample_report()

    class FailingService:
        async def run_keepalive(self, **_: object) -> KeepaliveReport:
            raise UpstreamError("failed before report generation")

    runtime = AppRuntime(build_settings())
    runtime._last_report = previous

    async def build_service() -> FailingService:
        return FailingService()

    monkeypatch.setattr(runtime, "_build_service", build_service)
    await runtime.start()
    try:
        with pytest.raises(UpstreamError):
            await runtime.run_keepalive()
    finally:
        await runtime.close()

    assert runtime.run_in_progress is False
    assert runtime.last_report is previous
