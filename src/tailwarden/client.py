from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

import aiohttp
from pydantic import SecretStr

from .errors import UpstreamPayloadError
from .http import request, request_json
from .models import Device, RecoveryKey

TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"


class TailscaleClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: SecretStr | str,
        *,
        base_url: str = TAILSCALE_API_BASE,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> None:
        raw_token = token.get_secret_value() if isinstance(token, SecretStr) else token
        if not raw_token.strip():
            raise ValueError("Tailscale token cannot be empty")
        if not base_url.strip():
            raise ValueError("Tailscale API base URL cannot be empty")

        self._session = session
        self._timeout = timeout or session.timeout
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {raw_token.strip()}",
        }

    async def list_devices(self, tailnet: str) -> list[Device]:
        payload = await request_json(
            self._session,
            method="GET",
            url=(f"{self._base_url}/tailnet/{quote(tailnet, safe='')}/devices?fields=all"),
            service="Tailscale devices",
            timeout=self._timeout,
            headers=self._headers,
            payload_message="Tailscale devices response is not valid JSON",
        )
        if not isinstance(payload, Mapping):
            raise UpstreamPayloadError(
                service="Tailscale devices",
                message="response must be a JSON object",
                payload=payload,
            )
        devices_payload = payload.get("devices")
        if not isinstance(devices_payload, list):
            raise UpstreamPayloadError(
                service="Tailscale devices",
                message="response is missing a devices list",
                payload=payload,
            )
        return [Device.from_tailscale_payload(item) for item in devices_payload]

    async def disable_key_expiry(self, device_id: str) -> None:
        await request(
            self._session,
            method="POST",
            url=f"{self._base_url}/device/{quote(device_id, safe='')}/key",
            service="Tailscale disable key expiry",
            timeout=self._timeout,
            headers={**self._headers, "Content-Type": "application/json"},
            json_body={"keyExpiryDisabled": True},
        )

    async def create_recovery_key(
        self,
        tailnet: str,
        tag: str,
        expiry_seconds: int,
        description: str,
    ) -> RecoveryKey:
        payload = await request_json(
            self._session,
            method="POST",
            url=f"{self._base_url}/tailnet/{quote(tailnet, safe='')}/keys",
            service="Tailscale auth keys",
            timeout=self._timeout,
            headers={**self._headers, "Content-Type": "application/json"},
            json_body={
                "capabilities": {
                    "devices": {
                        "create": {
                            "reusable": False,
                            "ephemeral": False,
                            "preauthorized": True,
                            "tags": [tag],
                        }
                    }
                },
                "expirySeconds": expiry_seconds,
                "description": description,
            },
            payload_message="Tailscale auth-key response is not valid JSON",
        )
        if not isinstance(payload, Mapping):
            raise UpstreamPayloadError(
                service="Tailscale auth keys",
                message="response must be a JSON object",
                payload=payload,
            )
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise UpstreamPayloadError(
                service="Tailscale auth keys",
                message="response is missing a non-empty key field",
                payload=payload,
            )
        return RecoveryKey(action="created", key=key.strip(), tag=tag)


__all__ = ["TAILSCALE_API_BASE", "TailscaleClient"]
