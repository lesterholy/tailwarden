from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from pydantic import SecretStr

from .config import Settings
from .errors import ConfigurationError, UpstreamHTTPError, UpstreamPayloadError
from .http import build_timeout, request_json

GITHUB_OIDC_SERVICE = "GitHub OIDC"
TAILSCALE_EXCHANGE_SERVICE = "Tailscale token exchange"
TAILSCALE_EXCHANGE_URL = "https://api.tailscale.com/api/v2/oauth/token-exchange"
TAILSCALE_OAUTH_SERVICE = "Tailscale OAuth"
TAILSCALE_OAUTH_URL = "https://api.tailscale.com/api/v2/oauth/token"


def _append_audience(url: str, audience: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "audience"
    ]
    query.append(("audience", audience))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _claims_summary(jwt_token: str) -> str:
    parts = jwt_token.split(".")
    if len(parts) < 2 or not parts[1]:
        return "GitHub OIDC claims unavailable"

    encoded = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        decoded = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return "GitHub OIDC claims unavailable"
    if not isinstance(decoded, Mapping):
        return "GitHub OIDC claims unavailable"

    safe_claims = {
        name: decoded[name]
        for name in ("sub", "aud", "repository", "ref", "environment")
        if name in decoded
    }
    if not safe_claims:
        return "GitHub OIDC claims unavailable"
    return f"GitHub OIDC claims: {json.dumps(safe_claims, sort_keys=True)}"


def _claims_diagnostic(settings: Settings, jwt_token: str) -> str:
    if settings.github_actions:
        return ""
    return f". {_claims_summary(jwt_token)}"


async def fetch_github_oidc_jwt(
    session: aiohttp.ClientSession,
    settings: Settings,
) -> SecretStr:
    settings.require_oidc_exchange()
    assert settings.actions_id_token_request_token is not None
    assert settings.actions_id_token_request_url is not None
    assert settings.ts_audience is not None

    payload = await request_json(
        session,
        method="GET",
        url=_append_audience(settings.actions_id_token_request_url, settings.ts_audience),
        service=GITHUB_OIDC_SERVICE,
        timeout=build_timeout(settings),
        headers={
            "Accept": "application/json",
            "Authorization": (
                f"Bearer {settings.actions_id_token_request_token.get_secret_value()}"
            ),
        },
        payload_message="GitHub OIDC response is not valid JSON",
    )
    if not isinstance(payload, Mapping):
        raise UpstreamPayloadError(
            service=GITHUB_OIDC_SERVICE,
            message="GitHub OIDC response is not a JSON object",
            payload=payload,
        )
    jwt_token = payload.get("value")
    if not isinstance(jwt_token, str) or not jwt_token.strip():
        raise UpstreamPayloadError(
            service=GITHUB_OIDC_SERVICE,
            message="GitHub OIDC response missing a non-empty value field",
            payload=payload,
        )
    return SecretStr(jwt_token.strip())


async def exchange_github_oidc_jwt(
    session: aiohttp.ClientSession,
    settings: Settings,
    jwt_token: SecretStr,
) -> SecretStr:
    settings.require_oidc_exchange()
    raw_jwt = jwt_token.get_secret_value()
    try:
        payload = await request_json(
            session,
            method="POST",
            url=TAILSCALE_EXCHANGE_URL,
            service=TAILSCALE_EXCHANGE_SERVICE,
            timeout=build_timeout(settings),
            headers={"Accept": "application/json"},
            data={"client_id": settings.ts_client_id, "jwt": raw_jwt},
            payload_message="Tailscale token exchange response is not valid JSON",
        )
    except UpstreamHTTPError as exc:
        raise UpstreamHTTPError(
            service=exc.service,
            status_code=exc.status_code,
            message=f"{exc.message}{_claims_diagnostic(settings, raw_jwt)}",
            body=exc.body,
        ) from exc
    except UpstreamPayloadError as exc:
        raise UpstreamPayloadError(
            service=exc.service,
            message=f"{exc.message}{_claims_diagnostic(settings, raw_jwt)}",
            payload=exc.payload,
        ) from exc

    if not isinstance(payload, Mapping):
        raise UpstreamPayloadError(
            service=TAILSCALE_EXCHANGE_SERVICE,
            message=f"expected a JSON object{_claims_diagnostic(settings, raw_jwt)}",
            payload=payload,
        )
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise UpstreamPayloadError(
            service=TAILSCALE_EXCHANGE_SERVICE,
            message=f"response missing access_token{_claims_diagnostic(settings, raw_jwt)}",
            payload=payload,
        )
    return SecretStr(access_token.strip())


async def exchange_tailscale_token(
    session: aiohttp.ClientSession,
    settings: Settings,
) -> SecretStr:
    jwt_token = await fetch_github_oidc_jwt(session, settings)
    return await exchange_github_oidc_jwt(session, settings, jwt_token)


async def fetch_oauth_access_token(
    session: aiohttp.ClientSession,
    settings: Settings,
) -> SecretStr:
    if not settings.has_oauth_client_config:
        raise ConfigurationError(
            "TS_OAUTH_CLIENT_ID and TS_OAUTH_CLIENT_SECRET are both required for OAuth"
        )
    assert settings.ts_oauth_client_secret is not None
    try:
        payload = await request_json(
            session,
            method="POST",
            url=TAILSCALE_OAUTH_URL,
            service=TAILSCALE_OAUTH_SERVICE,
            timeout=build_timeout(settings),
            headers={"Accept": "application/json"},
            data={
                "grant_type": "client_credentials",
                "client_id": settings.ts_oauth_client_id,
                "client_secret": settings.ts_oauth_client_secret.get_secret_value(),
            },
        )
    except UpstreamHTTPError as exc:
        # Token endpoint responses can echo credentials; keep only the HTTP status.
        raise UpstreamHTTPError(
            service=TAILSCALE_OAUTH_SERVICE,
            status_code=exc.status_code,
            message="OAuth token request rejected; check client credentials and permissions",
        ) from None
    except UpstreamPayloadError:
        raise UpstreamPayloadError(
            service=TAILSCALE_OAUTH_SERVICE,
            message="response is not valid JSON",
        ) from None

    access_token = payload.get("access_token") if isinstance(payload, Mapping) else None
    if not isinstance(access_token, str) or not access_token.strip():
        raise UpstreamPayloadError(
            service=TAILSCALE_OAUTH_SERVICE,
            message="response missing a non-empty access_token",
        )
    return SecretStr(access_token.strip())


async def resolve_tailscale_token(
    settings: Settings,
    session: aiohttp.ClientSession,
) -> SecretStr:
    if settings.has_oidc_exchange_config:
        return await exchange_tailscale_token(session, settings)
    if settings.github_actions:
        raise ConfigurationError(
            "GitHub Actions requires complete OIDC settings; TS_TOKEN fallback is disabled, "
            "as is OAuth client fallback"
        )
    if settings.ts_oauth_client_id or settings.ts_oauth_client_secret:
        return await fetch_oauth_access_token(session, settings)
    if settings.ts_token is not None:
        return settings.ts_token
    raise ConfigurationError(
        "configure complete GitHub OIDC settings or provide "
        "TS_OAUTH_CLIENT_ID and TS_OAUTH_CLIENT_SECRET (or TS_TOKEN) for local use"
    )


__all__ = [
    "TAILSCALE_EXCHANGE_URL",
    "TAILSCALE_OAUTH_URL",
    "exchange_github_oidc_jwt",
    "exchange_tailscale_token",
    "fetch_github_oidc_jwt",
    "fetch_oauth_access_token",
    "resolve_tailscale_token",
]
