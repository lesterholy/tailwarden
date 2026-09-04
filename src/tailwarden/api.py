from __future__ import annotations

from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, StrictBool

from .config import Settings
from .errors import ConfigurationError, RunInProgressError, UpstreamError
from .models import KeepaliveReport, RecoveryKeyResponse
from .runtime import AppRuntime

_bearer_scheme = HTTPBearer(auto_error=False)


class KeepaliveRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: StrictBool | None = None
    fail_on_stale: StrictBool | None = None


def _get_runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime


def _require_management_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    runtime: Annotated[AppRuntime, Depends(_get_runtime)],
) -> None:
    expected = runtime.settings.app_api_token
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="management API is not configured",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not compare_digest(credentials.credentials, expected.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


RuntimeDependency = Annotated[AppRuntime, Depends(_get_runtime)]
ManagementAuth = Annotated[None, Depends(_require_management_token)]


def create_app(
    settings: Settings | None = None,
    runtime: AppRuntime | None = None,
) -> FastAPI:
    if settings is not None and runtime is not None and settings != runtime.settings:
        raise ValueError("settings and runtime.settings must match")
    resolved_runtime = runtime or AppRuntime(settings or Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = resolved_runtime
        await resolved_runtime.start()
        try:
            yield
        finally:
            await resolved_runtime.close()

    app = FastAPI(title="Tailwarden", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ConfigurationError)
    async def handle_configuration_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RunInProgressError)
    async def handle_run_in_progress(_: Request, exc: RunInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UpstreamError)
    async def handle_upstream_error(_: Request, exc: UpstreamError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(runtime: RuntimeDependency) -> dict[str, str]:
        if not runtime.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tailscale credentials are not configured",
            )
        return {"status": "ready"}

    @app.get("/api/v1/status")
    async def status_endpoint(
        runtime: RuntimeDependency,
        _authorized: ManagementAuth,
    ) -> dict[str, object]:
        runtime_status = runtime.status()
        return {
            "service": runtime_status.service,
            "ready": runtime_status.ready,
            "run_in_progress": runtime_status.run_in_progress,
            "management_api_configured": runtime_status.management_api_configured,
            "last_report": (
                None
                if runtime_status.last_report is None
                else runtime_status.last_report.model_dump(mode="json")
            ),
        }

    @app.post("/api/v1/keepalive/run", response_model=KeepaliveReport)
    async def run_keepalive(
        runtime: RuntimeDependency,
        _authorized: ManagementAuth,
        payload: KeepaliveRunRequest | None = None,
    ) -> KeepaliveReport:
        return await runtime.run_keepalive(
            dry_run=None if payload is None else payload.dry_run,
            fail_on_stale=None if payload is None else payload.fail_on_stale,
        )

    @app.post("/api/v1/recovery-keys", response_model=RecoveryKeyResponse)
    async def create_recovery_key(
        response: Response,
        runtime: RuntimeDependency,
        _authorized: ManagementAuth,
    ) -> RecoveryKeyResponse:
        recovery_key = await runtime.create_recovery_key()
        response.headers["Cache-Control"] = "no-store"
        return RecoveryKeyResponse.from_recovery_key(recovery_key)

    return app


__all__ = ["KeepaliveRunRequest", "create_app"]
