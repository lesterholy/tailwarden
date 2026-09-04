from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class TailwardenError(Exception):
    """Base exception for the tailwarden package."""


class ConfigurationError(TailwardenError):
    """Raised when configuration values are missing or invalid for an operation."""


class UpstreamError(TailwardenError):
    """Raised when an upstream service fails or returns invalid data."""


class UpstreamHTTPError(UpstreamError):
    """Raised when an upstream HTTP request returns a non-success status."""

    def __init__(
        self,
        *,
        service: str,
        status_code: int,
        message: str,
        body: str | None = None,
    ) -> None:
        self.service = service
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"{service} request failed with HTTP {status_code}: {message}")


class UpstreamPayloadError(UpstreamError):
    """Raised when upstream data cannot be parsed or normalized safely."""

    def __init__(self, *, service: str, message: str, payload: Any | None = None) -> None:
        self.service = service
        self.message = message
        self.payload = payload
        super().__init__(f"{service} payload invalid: {message}")


class RunInProgressError(TailwardenError):
    """Raised when a keepalive run is already active and another run should not start."""

    def __init__(self, *, lock_name: str, owner: str | None = None) -> None:
        self.lock_name = lock_name
        self.owner = owner
        details = f" ({owner})" if owner else ""
        super().__init__(f"run already in progress for {lock_name}{details}")


ErrorContext = Mapping[str, Any]
