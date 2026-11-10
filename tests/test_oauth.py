from __future__ import annotations

from functools import partial

import aiohttp
import pytest
from aiohttp import web

import tailwarden.auth as auth_module
import tailwarden.runtime as runtime_module
from tailwarden.auth import resolve_tailscale_token
from tailwarden.config import Settings
from tailwarden.errors import ConfigurationError, UpstreamHTTPError, UpstreamPayloadError
from tailwarden.models import KeepaliveOutcome
from tailwarden.runtime import AppRuntime


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TS_OAUTH_CLIENT_ID": "oauth-client",
        "TS_OAUTH_CLIENT_SECRET": "private-client-secret",
        "TS_TOKEN": "unused-static-token",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_oauth_posts_client_credentials_and_takes_precedence_over_static_token(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    async def token(request: web.Request) -> web.Response:
        received["form"] = dict(await request.post())
        received["query"] = dict(request.query)
        received["content_type"] = request.content_type
        received["authorization"] = request.headers.get("Authorization")
        return web.json_response(
            {"access_token": " fresh-token ", "token_type": "Bearer", "expires_in": 3600}
        )

    app = web.Application()
    app.router.add_post("/oauth/token", token)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_OAUTH_URL", f"{base_url}/oauth/token")

    async with aiohttp.ClientSession() as session:
        result = await resolve_tailscale_token(build_settings(), session)

    assert result.get_secret_value() == "fresh-token"
    assert "fresh-token" not in repr(result)
    assert received == {
        "form": {
            "grant_type": "client_credentials",
            "client_id": "oauth-client",
            "client_secret": "private-client-secret",
        },
        "query": {},
        "content_type": "application/x-www-form-urlencoded",
        "authorization": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 500])
async def test_oauth_errors_do_not_retry_fall_back_or_expose_response_secrets(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    calls = 0

    async def token(_: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.json_response(
            {"error_description": "private-client-secret rejected"}, status=status
        )

    app = web.Application()
    app.router.add_post("/oauth/token", token)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_OAUTH_URL", f"{base_url}/oauth/token")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamHTTPError) as caught:
            await resolve_tailscale_token(build_settings(), session)

    assert calls == 1
    assert caught.value.status_code == status
    assert "private-client-secret" not in str(caught.value)
    assert caught.value.body is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", ["not-json", "null", "[]", "{}", '{"access_token": " "}', '{"access_token": 123}']
)
async def test_oauth_rejects_invalid_token_responses(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    async def token(_: web.Request) -> web.Response:
        return web.Response(text=body, content_type="application/json")

    app = web.Application()
    app.router.add_post("/oauth/token", token)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_OAUTH_URL", f"{base_url}/oauth/token")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamPayloadError) as caught:
            await resolve_tailscale_token(build_settings(), session)

    assert caught.value.payload is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["TS_OAUTH_CLIENT_ID", "TS_OAUTH_CLIENT_SECRET"])
async def test_partial_oauth_is_not_ready_and_does_not_fall_back_to_static_token(
    missing: str,
) -> None:
    runtime = AppRuntime(build_settings(**{missing: ""}))
    await runtime.start()
    try:
        assert runtime.ready is False
        with pytest.raises(ConfigurationError, match="both required for OAuth"):
            await runtime.run_keepalive()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_live_runtime_fetches_fresh_oauth_tokens_for_keepalive_and_recovery(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_count = 0
    authorizations: list[str] = []
    key_requests: list[dict[str, object]] = []

    async def token(_: web.Request) -> web.Response:
        nonlocal token_count
        token_count += 1
        return web.json_response({"access_token": f"access-{token_count}", "expires_in": 3600})

    async def devices(request: web.Request) -> web.Response:
        authorizations.append(request.headers["Authorization"])
        return web.json_response({"devices": []})

    async def key(request: web.Request) -> web.Response:
        authorizations.append(request.headers["Authorization"])
        key_requests.append(await request.json())
        return web.json_response({"key": "tskey-auth-one-shot"})

    app = web.Application()
    app.router.add_post("/oauth/token", token)
    app.router.add_get("/tailnet/-/devices", devices)
    app.router.add_post("/tailnet/-/keys", key)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_OAUTH_URL", f"{base_url}/oauth/token")
    monkeypatch.setattr(
        runtime_module,
        "TailscaleClient",
        partial(runtime_module.TailscaleClient, base_url=base_url),
    )
    runtime = AppRuntime(build_settings(TS_TOKEN=""))
    await runtime.start()
    try:
        assert runtime.ready is True
        assert (await runtime.run_keepalive()).outcome is KeepaliveOutcome.NO_MATCHES
        recovery = await runtime.create_recovery_key()
        assert (await runtime.run_keepalive()).outcome is KeepaliveOutcome.NO_MATCHES
    finally:
        await runtime.close()

    assert runtime.ready is False
    assert recovery.key.get_secret_value() == "tskey-auth-one-shot"
    assert authorizations == ["Bearer access-1", "Bearer access-2", "Bearer access-3"]
    assert token_count == 3
    assert len(key_requests) == 1
    assert key_requests[0]["expirySeconds"] == 3600
    assert key_requests[0]["capabilities"] == {
        "devices": {
            "create": {
                "reusable": False,
                "ephemeral": False,
                "preauthorized": True,
                "tags": ["tag:tailwarden-managed"],
            }
        }
    }
