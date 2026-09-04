from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiohttp

from .auth import resolve_tailscale_token
from .client import TailscaleClient
from .config import Settings
from .errors import RunInProgressError
from .http import build_timeout
from .models import KeepaliveReport, RecoveryKey
from .service import KeepaliveService


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    service: str
    ready: bool
    run_in_progress: bool
    management_api_configured: bool
    last_report: KeepaliveReport | None


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._operation_lock = asyncio.Lock()
        self._operation_owner: str | None = None
        self._last_report: KeepaliveReport | None = None

    @property
    def ready(self) -> bool:
        session_ready = self._session is not None and not self._session.closed
        credentials_ready = self.settings.has_oidc_exchange_config or (
            not self.settings.github_actions and self.settings.has_static_tailscale_token
        )
        return session_ready and credentials_ready

    @property
    def run_in_progress(self) -> bool:
        return self._operation_lock.locked()

    @property
    def last_report(self) -> KeepaliveReport | None:
        return self._last_report

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=build_timeout(self.settings))

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            service="tailwarden",
            ready=self.ready,
            run_in_progress=self.run_in_progress,
            management_api_configured=self.settings.app_api_token is not None,
            last_report=self._last_report,
        )

    @asynccontextmanager
    async def _exclusive_operation(self, *, owner: str):
        if self._operation_lock.locked():
            raise RunInProgressError(
                lock_name="tailscale-operation",
                owner=self._operation_owner,
            )
        await self._operation_lock.acquire()
        self._operation_owner = owner
        try:
            yield
        finally:
            self._operation_owner = None
            self._operation_lock.release()

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("runtime has not been started")
        return self._session

    async def _build_service(self) -> KeepaliveService:
        session = self._require_session()
        token = await resolve_tailscale_token(self.settings, session)
        client = TailscaleClient(
            session,
            token,
            timeout=build_timeout(self.settings),
        )
        return KeepaliveService(self.settings, client)

    async def run_keepalive(
        self,
        *,
        dry_run: bool | None = None,
        fail_on_stale: bool | None = None,
    ) -> KeepaliveReport:
        async with self._exclusive_operation(owner="keepalive"):
            service = await self._build_service()
            report = await service.run_keepalive(
                dry_run=dry_run,
                fail_on_stale=fail_on_stale,
            )
            self._last_report = report
            return report

    async def create_recovery_key(self) -> RecoveryKey:
        async with self._exclusive_operation(owner="recovery-key"):
            service = await self._build_service()
            return await service.create_recovery_key()


__all__ = ["AppRuntime", "RuntimeStatus"]
