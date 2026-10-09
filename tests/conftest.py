from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from aiohttp import web

from tailwarden.config import Settings

ServerFactory = Callable[[web.Application], Awaitable[str]]


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let each test opt into settings instead of inheriting host or CI values."""
    for field in Settings.model_fields.values():
        if isinstance(field.alias, str):
            monkeypatch.delenv(field.alias, raising=False)


@pytest.fixture
async def aiohttp_server(
    unused_tcp_port_factory: Callable[[], int],
) -> AsyncIterator[ServerFactory]:
    runners: list[web.AppRunner] = []

    async def start(application: web.Application) -> str:
        port = unused_tcp_port_factory()
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return f"http://127.0.0.1:{port}"

    yield start

    for runner in reversed(runners):
        await runner.cleanup()
