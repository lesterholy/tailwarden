from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from .config import Settings
from .errors import UpstreamError, UpstreamHTTPError, UpstreamPayloadError

_MAX_DIAGNOSTIC_LENGTH = 4096


@dataclass(slots=True)
class ResponseBody:
    text: str
    json_value: Any | None


def build_timeout(settings: Settings) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=settings.http_total_timeout_seconds,
        connect=settings.http_connect_timeout_seconds,
        sock_connect=settings.http_connect_timeout_seconds,
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_diagnostic(value: str) -> str:
    escaped = "".join(
        character if ord(character) >= 32 and ord(character) != 127 else f"\\x{ord(character):02x}"
        for character in value
    )
    if len(escaped) <= _MAX_DIAGNOSTIC_LENGTH:
        return escaped
    return escaped[:_MAX_DIAGNOSTIC_LENGTH] + "..."


def _extract_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, Mapping):
        for key in ("message", "error_description", "error", "detail", "title"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _safe_diagnostic(value.strip())
            if value not in (None, "", [], {}):
                return _safe_diagnostic(_json_dumps(value))
        return _safe_diagnostic(_json_dumps(dict(payload)))
    if payload not in (None, "", [], {}):
        if isinstance(payload, str):
            return _safe_diagnostic(payload.strip()) or fallback
        return _safe_diagnostic(_json_dumps(payload))
    normalized = fallback.strip()
    return _safe_diagnostic(normalized) or "upstream request failed"


async def read_response_body(response: aiohttp.ClientResponse) -> ResponseBody:
    raw = await response.read()
    if not raw:
        return ResponseBody(text="", json_value=None)

    charset = response.charset or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    stripped = text.strip()
    if not stripped:
        return ResponseBody(text=text, json_value=None)

    try:
        json_value = json.loads(stripped)
    except json.JSONDecodeError:
        json_value = None

    return ResponseBody(text=text[:_MAX_DIAGNOSTIC_LENGTH], json_value=json_value)


async def request(
    session: aiohttp.ClientSession,
    *,
    method: str,
    url: str,
    service: str,
    timeout: aiohttp.ClientTimeout,
    headers: Mapping[str, str] | None = None,
    json_body: Any | None = None,
    data: Any | None = None,
) -> ResponseBody:
    try:
        async with session.request(
            method,
            url,
            headers=dict(headers or {}),
            json=json_body,
            data=data,
            timeout=timeout,
        ) as response:
            body = await read_response_body(response)
            if 200 <= response.status < 300:
                return body

            raise UpstreamHTTPError(
                service=service,
                status_code=response.status,
                message=_extract_message(body.json_value, body.text),
                body=body.text or None,
            )
    except UpstreamHTTPError:
        raise
    except TimeoutError as exc:
        raise UpstreamError(f"{service} request timed out") from exc
    except aiohttp.ClientError as exc:
        raise UpstreamError(f"{service} transport error: {exc}") from exc


async def request_json(
    session: aiohttp.ClientSession,
    *,
    method: str,
    url: str,
    service: str,
    timeout: aiohttp.ClientTimeout,
    headers: Mapping[str, str] | None = None,
    json_body: Any | None = None,
    data: Any | None = None,
    payload_message: str = "expected a JSON response body",
) -> Any:
    body = await request(
        session,
        method=method,
        url=url,
        service=service,
        timeout=timeout,
        headers=headers,
        json_body=json_body,
        data=data,
    )
    if body.json_value is None:
        raise UpstreamPayloadError(
            service=service,
            message=payload_message,
            payload=body.text or None,
        )
    return body.json_value


__all__ = ["ResponseBody", "build_timeout", "read_response_body", "request", "request_json"]
