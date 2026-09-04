from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from tailwarden.api import create_app
from tailwarden.config import Settings
from tailwarden.errors import RunInProgressError, UpstreamError
from tailwarden.models import KeepaliveOutcome, KeepaliveReport, RecoveryKey
from tailwarden.runtime import AppRuntime


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_API_TOKEN": "app-secret",
        "TS_TOKEN": "short-lived-token",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def sample_report(**overrides: object) -> KeepaliveReport:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "outcome": KeepaliveOutcome.HEALTHY,
        "tailnet": "-",
        "server_tag": "tag:server",
        "rejoin_auth_key_tag": "tag:server",
        "dry_run": False,
        "fail_on_stale": True,
        "stale_after_minutes": 30,
        "key_expiry_warning_hours": 168,
        "auth_key_expiry_seconds": 3600,
        "started_at": now,
        "finished_at": now,
    }
    values.update(overrides)
    return KeepaliveReport(**values)


def auth_headers(token: str = "app-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_health_and_ready_endpoints() -> None:
    app = create_app(build_settings())
    async with app_client(app) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_ready_returns_503_without_tailscale_credentials() -> None:
    app = create_app(build_settings(TS_TOKEN=""))
    async with app_client(app) as client:
        response = await client.get("/readyz")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_management_endpoints_require_correct_bearer_token() -> None:
    app = create_app(build_settings())
    async with app_client(app) as client:
        missing = await client.get("/api/v1/status")
        wrong = await client.get("/api/v1/status", headers=auth_headers("wrong"))
        correct = await client.get("/api/v1/status", headers=auth_headers())

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert correct.status_code == 200
    assert correct.json()["management_api_configured"] is True


@pytest.mark.asyncio
async def test_management_endpoint_returns_503_when_api_token_is_unconfigured() -> None:
    app = create_app(build_settings(APP_API_TOKEN=""))
    async with app_client(app) as client:
        response = await client.get("/api/v1/status")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_keepalive_endpoint_passes_strict_boolean_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(build_settings())
    received: dict[str, bool | None] = {}

    async with app_client(app) as client:
        runtime = app.state.runtime

        async def fake_run_keepalive(
            *,
            dry_run: bool | None = None,
            fail_on_stale: bool | None = None,
        ) -> KeepaliveReport:
            received.update(dry_run=dry_run, fail_on_stale=fail_on_stale)
            return sample_report(dry_run=dry_run, fail_on_stale=fail_on_stale)

        monkeypatch.setattr(runtime, "run_keepalive", fake_run_keepalive)
        valid = await client.post(
            "/api/v1/keepalive/run",
            headers=auth_headers(),
            json={"dry_run": True, "fail_on_stale": False},
        )
        string_boolean = await client.post(
            "/api/v1/keepalive/run",
            headers=auth_headers(),
            json={"dry_run": "true"},
        )
        extra_field = await client.post(
            "/api/v1/keepalive/run",
            headers=auth_headers(),
            json={"unexpected": True},
        )

    assert valid.status_code == 200
    assert received == {"dry_run": True, "fail_on_stale": False}
    assert string_boolean.status_code == 422
    assert extra_field.status_code == 422


@pytest.mark.asyncio
async def test_keepalive_endpoint_maps_concurrent_run_to_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(build_settings())
    async with app_client(app) as client:

        async def busy_run(**_: object) -> KeepaliveReport:
            raise RunInProgressError(lock_name="tailscale-operation", owner="keepalive")

        monkeypatch.setattr(app.state.runtime, "run_keepalive", busy_run)
        response = await client.post(
            "/api/v1/keepalive/run",
            headers=auth_headers(),
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_keepalive_endpoint_maps_upstream_error_to_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(build_settings())
    async with app_client(app) as client:

        async def failed_run(**_: object) -> KeepaliveReport:
            raise UpstreamError("Tailscale transport error")

        monkeypatch.setattr(app.state.runtime, "run_keepalive", failed_run)
        response = await client.post(
            "/api/v1/keepalive/run",
            headers=auth_headers(),
        )

    assert response.status_code == 502
    assert "transport error" in response.json()["detail"]


@pytest.mark.asyncio
async def test_recovery_key_is_plaintext_only_in_authorized_no_store_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(build_settings())
    calls = 0

    async with app_client(app) as client:

        async def fake_create_recovery_key() -> RecoveryKey:
            nonlocal calls
            calls += 1
            return RecoveryKey(
                action="created",
                key="tskey-auth-k123",
                tag="tag:server",
            )

        monkeypatch.setattr(app.state.runtime, "create_recovery_key", fake_create_recovery_key)
        unauthorized = await client.post("/api/v1/recovery-keys")
        authorized = await client.post(
            "/api/v1/recovery-keys",
            headers=auth_headers(),
        )
        status_response = await client.get("/api/v1/status", headers=auth_headers())

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.headers["Cache-Control"] == "no-store"
    assert authorized.json()["key"] == "tskey-auth-k123"
    assert "tskey-auth-k123" not in status_response.text
    assert calls == 1


@pytest.mark.asyncio
async def test_create_app_accepts_an_injected_runtime() -> None:
    runtime = AppRuntime(build_settings())
    app = create_app(runtime=runtime)

    async with app_client(app):
        assert app.state.runtime is runtime
