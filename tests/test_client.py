from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp import web
from pydantic import SecretStr

from tailwarden.client import TailscaleClient
from tailwarden.errors import UpstreamError, UpstreamHTTPError, UpstreamPayloadError


def make_client(
    session: aiohttp.ClientSession,
    base_url: str,
) -> TailscaleClient:
    return TailscaleClient(
        session,
        SecretStr("short-lived-token"),
        base_url=f"{base_url}/api/v2",
        timeout=aiohttp.ClientTimeout(total=2, connect=1),
    )


@pytest.mark.asyncio
async def test_list_devices_uses_encoded_tailnet_and_parses_payload(aiohttp_server) -> None:
    received: dict[str, str] = {}

    async def list_devices(request: web.Request) -> web.Response:
        received["tailnet"] = request.match_info["tailnet"]
        received["fields"] = request.query["fields"]
        received["authorization"] = request.headers["Authorization"]
        return web.json_response(
            {
                "devices": [
                    {
                        "nodeId": "node-1",
                        "hostname": "server-1",
                        "tags": ["tag:server"],
                        "online": True,
                        "keyExpiryDisabled": True,
                    }
                ]
            }
        )

    app = web.Application()
    app.router.add_get("/api/v2/tailnet/{tailnet}/devices", list_devices)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        devices = await make_client(session, base_url).list_devices("owner@example.com")

    assert [device.id for device in devices] == ["node-1"]
    assert received == {
        "tailnet": "owner@example.com",
        "fields": "all",
        "authorization": "Bearer short-lived-token",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_kind", "payload"),
    [
        ("text", "not-json"),
        ("json", []),
        ("json", {"devices": "not-a-list"}),
        ("json", {"devices": ["not-an-object"]}),
    ],
)
async def test_list_devices_rejects_malformed_success_payloads(
    aiohttp_server,
    response_kind: str,
    payload: object,
) -> None:
    async def list_devices(_: web.Request) -> web.Response:
        if response_kind == "text":
            return web.Response(text=str(payload))
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/api/v2/tailnet/{tailnet}/devices", list_devices)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamPayloadError):
            await make_client(session, base_url).list_devices("-")


@pytest.mark.asyncio
async def test_disable_key_expiry_only_succeeds_after_real_2xx(aiohttp_server) -> None:
    received: dict[str, object] = {}

    async def disable(request: web.Request) -> web.Response:
        received["id"] = request.match_info["device_id"]
        received["body"] = await request.json()
        return web.Response(status=204)

    app = web.Application()
    app.router.add_post("/api/v2/device/{device_id}/key", disable)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        await make_client(session, base_url).disable_key_expiry("node/id")

    assert received == {
        "id": "node/id",
        "body": {"keyExpiryDisabled": True},
    }


@pytest.mark.asyncio
async def test_disable_key_expiry_surfaces_plain_403_body(aiohttp_server) -> None:
    async def disable(_: web.Request) -> web.Response:
        return web.Response(status=403, text="credential cannot edit device")

    app = web.Application()
    app.router.add_post("/api/v2/device/{device_id}/key", disable)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamHTTPError) as caught:
            await make_client(session, base_url).disable_key_expiry("node-1")

    assert caught.value.status_code == 403
    assert caught.value.message == "credential cannot edit device"


@pytest.mark.asyncio
async def test_create_recovery_key_sends_expected_one_time_key_payload(aiohttp_server) -> None:
    received: dict[str, object] = {}

    async def create_key(request: web.Request) -> web.Response:
        received.update(await request.json())
        return web.json_response({"key": "tskey-auth-k123"})

    app = web.Application()
    app.router.add_post("/api/v2/tailnet/{tailnet}/keys", create_key)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        recovery = await make_client(session, base_url).create_recovery_key(
            "-",
            "tag:web",
            900,
            "controlled recovery",
        )

    assert received == {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": False,
                    "preauthorized": True,
                    "tags": ["tag:web"],
                }
            }
        },
        "expirySeconds": 900,
        "description": "controlled recovery",
    }
    assert recovery.key.get_secret_value() == "tskey-auth-k123"
    assert recovery.model_dump()["key"] == "********"


@pytest.mark.asyncio
async def test_create_recovery_key_rejects_missing_key(aiohttp_server) -> None:
    async def create_key(_: web.Request) -> web.Response:
        return web.json_response({"key": ""})

    app = web.Application()
    app.router.add_post("/api/v2/tailnet/{tailnet}/keys", create_key)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamPayloadError):
            await make_client(session, base_url).create_recovery_key(
                "-",
                "tag:server",
                3600,
                "recovery",
            )


@pytest.mark.asyncio
async def test_transport_error_is_wrapped_as_upstream_error(unused_tcp_port: int) -> None:
    base_url = f"http://127.0.0.1:{unused_tcp_port}"
    async with aiohttp.ClientSession() as session:
        with pytest.raises(UpstreamError, match="transport error"):
            await make_client(session, base_url).list_devices("-")


@pytest.mark.asyncio
async def test_request_timeout_is_wrapped_as_upstream_error(aiohttp_server) -> None:
    async def slow_response(_: web.Request) -> web.Response:
        await asyncio.sleep(0.1)
        return web.json_response({"devices": []})

    app = web.Application()
    app.router.add_get("/api/v2/tailnet/{tailnet}/devices", slow_response)
    base_url = await aiohttp_server(app)

    async with aiohttp.ClientSession() as session:
        client = TailscaleClient(
            session,
            "token",
            base_url=f"{base_url}/api/v2",
            timeout=aiohttp.ClientTimeout(total=0.01),
        )
        with pytest.raises(UpstreamError, match="timed out"):
            await client.list_devices("-")
