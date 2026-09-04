from __future__ import annotations

import base64
import json

import aiohttp
import pytest
from aiohttp import web

import tailwarden.auth as auth_module
from tailwarden.auth import (
    exchange_tailscale_token,
    fetch_github_oidc_jwt,
    resolve_tailscale_token,
)
from tailwarden.config import Settings
from tailwarden.errors import ConfigurationError, UpstreamHTTPError, UpstreamPayloadError


def build_settings(request_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "TS_CLIENT_ID": "client-123",
        "TS_AUDIENCE": "api.tailscale.com/client-123",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "github-request-token",
        "ACTIONS_ID_TOKEN_REQUEST_URL": request_url,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_jwt(**claims: object) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


@pytest.mark.asyncio
async def test_fetch_github_oidc_jwt_sends_bearer_and_audience(aiohttp_server) -> None:
    received: dict[str, str] = {}

    async def oidc(request: web.Request) -> web.Response:
        received["authorization"] = request.headers["Authorization"]
        received["audience"] = request.query["audience"]
        received["existing"] = request.query["existing"]
        return web.json_response({"value": "header.payload.signature"})

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    base_url = await aiohttp_server(app)
    settings = build_settings(f"{base_url}/oidc?existing=1")

    async with aiohttp.ClientSession() as session:
        token = await fetch_github_oidc_jwt(session, settings)

    assert token.get_secret_value() == "header.payload.signature"
    assert received == {
        "authorization": "Bearer github-request-token",
        "audience": "api.tailscale.com/client-123",
        "existing": "1",
    }


@pytest.mark.asyncio
async def test_fetch_github_oidc_jwt_rejects_non_json_success(aiohttp_server) -> None:
    async def oidc(_: web.Request) -> web.Response:
        return web.Response(text="not-json")

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    base_url = await aiohttp_server(app)
    settings = build_settings(f"{base_url}/oidc")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamPayloadError, match="not valid JSON"):
            await fetch_github_oidc_jwt(session, settings)


@pytest.mark.asyncio
async def test_fetch_github_oidc_jwt_preserves_plain_http_error(aiohttp_server) -> None:
    async def oidc(_: web.Request) -> web.Response:
        return web.Response(status=502, text="gateway failed\nretry later")

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    base_url = await aiohttp_server(app)
    settings = build_settings(f"{base_url}/oidc")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamHTTPError) as caught:
            await fetch_github_oidc_jwt(session, settings)

    assert caught.value.status_code == 502
    assert "gateway failed\\x0aretry later" in caught.value.message


@pytest.mark.asyncio
async def test_exchange_tailscale_token_posts_form_and_returns_secret(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt = make_jwt(sub="repo:owner/repo:environment:production")
    received: dict[str, str] = {}

    async def oidc(_: web.Request) -> web.Response:
        return web.json_response({"value": jwt})

    async def exchange(request: web.Request) -> web.Response:
        form = await request.post()
        received.update({key: str(value) for key, value in form.items()})
        return web.json_response({"access_token": "short-lived-token"})

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    app.router.add_post("/exchange", exchange)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_EXCHANGE_URL", f"{base_url}/exchange")
    settings = build_settings(f"{base_url}/oidc")

    async with aiohttp.ClientSession() as session:
        token = await exchange_tailscale_token(session, settings)

    assert token.get_secret_value() == "short-lived-token"
    assert received == {"client_id": "client-123", "jwt": jwt}


@pytest.mark.asyncio
async def test_exchange_error_handles_non_json_and_reports_safe_claims(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt = make_jwt(sub="repo:owner/repo:environment:production", aud="tailscale")

    async def oidc(_: web.Request) -> web.Response:
        return web.json_response({"value": jwt})

    async def exchange(_: web.Request) -> web.Response:
        return web.Response(status=403, text="trust credential rejected")

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    app.router.add_post("/exchange", exchange)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_EXCHANGE_URL", f"{base_url}/exchange")
    settings = build_settings(f"{base_url}/oidc")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamHTTPError) as caught:
            await exchange_tailscale_token(session, settings)

    assert caught.value.status_code == 403
    assert "trust credential rejected" in caught.value.message
    assert "repo:owner/repo:environment:production" in caught.value.message
    assert jwt not in str(caught.value)


@pytest.mark.asyncio
async def test_exchange_error_omits_claims_in_github_actions(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt = make_jwt(
        sub="repo:owner/private-repo:environment:production",
        aud="api.tailscale.com/private-client",
    )

    async def oidc(_: web.Request) -> web.Response:
        return web.json_response({"value": jwt})

    async def exchange(_: web.Request) -> web.Response:
        return web.Response(status=403, text="trust credential rejected")

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    app.router.add_post("/exchange", exchange)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_EXCHANGE_URL", f"{base_url}/exchange")
    settings = build_settings(f"{base_url}/oidc", GITHUB_ACTIONS=True)

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamHTTPError) as caught:
            await exchange_tailscale_token(session, settings)

    assert caught.value.message == "trust credential rejected"
    assert "private-repo" not in str(caught.value)
    assert "private-client" not in str(caught.value)
    assert jwt not in str(caught.value)


@pytest.mark.asyncio
async def test_exchange_rejects_success_without_access_token(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt = make_jwt(sub="repo:owner/repo:environment:production")

    async def oidc(_: web.Request) -> web.Response:
        return web.json_response({"value": jwt})

    async def exchange(_: web.Request) -> web.Response:
        return web.json_response({"token_type": "Bearer"})

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    app.router.add_post("/exchange", exchange)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_EXCHANGE_URL", f"{base_url}/exchange")
    settings = build_settings(f"{base_url}/oidc")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamPayloadError, match="missing access_token"):
            await exchange_tailscale_token(session, settings)


@pytest.mark.asyncio
async def test_resolve_prefers_complete_oidc_over_static_token(
    aiohttp_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"oidc": 0, "exchange": 0}

    async def oidc(_: web.Request) -> web.Response:
        calls["oidc"] += 1
        return web.json_response({"value": "header.payload.signature"})

    async def exchange(_: web.Request) -> web.Response:
        calls["exchange"] += 1
        return web.json_response({"access_token": "oidc-token"})

    app = web.Application()
    app.router.add_get("/oidc", oidc)
    app.router.add_post("/exchange", exchange)
    base_url = await aiohttp_server(app)
    monkeypatch.setattr(auth_module, "TAILSCALE_EXCHANGE_URL", f"{base_url}/exchange")
    settings = build_settings(f"{base_url}/oidc", TS_TOKEN="static-token")

    async with aiohttp.ClientSession() as session:
        token = await resolve_tailscale_token(settings, session)

    assert token.get_secret_value() == "oidc-token"
    assert calls == {"oidc": 1, "exchange": 1}


@pytest.mark.asyncio
async def test_resolve_uses_static_token_when_oidc_is_incomplete() -> None:
    settings = Settings(_env_file=None, TS_CLIENT_ID="partial", TS_TOKEN="static-token")
    async with aiohttp.ClientSession() as session:
        token = await resolve_tailscale_token(settings, session)
    assert token.get_secret_value() == "static-token"


@pytest.mark.asyncio
async def test_resolve_rejects_static_token_fallback_in_github_actions() -> None:
    settings = Settings(
        _env_file=None,
        GITHUB_ACTIONS=True,
        TS_CLIENT_ID="partial",
        TS_TOKEN="static-token",
    )
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ConfigurationError, match="TS_TOKEN fallback is disabled"):
            await resolve_tailscale_token(settings, session)


@pytest.mark.asyncio
async def test_resolve_requires_a_supported_credential_source() -> None:
    settings = Settings(_env_file=None)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ConfigurationError):
            await resolve_tailscale_token(settings, session)
