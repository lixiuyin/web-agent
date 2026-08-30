"""Auditable provider-endpoint availability checks for model studies."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field


class EndpointProbe(BaseModel):
    """One credential-free record of whether a requested model can be invoked."""

    schema_version: int = 1
    provider: str
    model: str
    endpoint_host: str
    status: Literal["available", "unavailable"]
    checked_at: str
    duration_seconds: float = Field(ge=0.0)
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None


def probe_chat_endpoint(
    *,
    api_url: str,
    api_key: str,
    provider: str,
    model: str,
    timeout_seconds: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> EndpointProbe:
    """Make one minimal real inference request before an expensive study run.

    HTTP errors are retained as endpoint evidence rather than converted into
    benchmark task failures.  API keys, full URLs, and response bodies are never
    written to the result.
    """
    started = time.monotonic()
    checked_at = datetime.now(UTC).isoformat()
    endpoint_host = urlsplit(api_url).netloc
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly READY."}],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
            response = client.post(api_url, headers=headers, json=payload)
        duration = time.monotonic() - started
        if response.is_success:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("choices"), list) and body["choices"]:
                return EndpointProbe(
                    provider=provider,
                    model=model,
                    endpoint_host=endpoint_host,
                    status="available",
                    checked_at=checked_at,
                    duration_seconds=duration,
                    status_code=response.status_code,
                )
            return EndpointProbe(
                provider=provider,
                model=model,
                endpoint_host=endpoint_host,
                status="unavailable",
                checked_at=checked_at,
                duration_seconds=duration,
                status_code=response.status_code,
                error_code="invalid_success_payload",
                error_message="successful response did not contain a non-empty choices array",
            )
        code, message = _provider_error(response)
        return EndpointProbe(
            provider=provider,
            model=model,
            endpoint_host=endpoint_host,
            status="unavailable",
            checked_at=checked_at,
            duration_seconds=duration,
            status_code=response.status_code,
            error_code=code,
            error_message=message,
        )
    except httpx.HTTPError as exc:
        return EndpointProbe(
            provider=provider,
            model=model,
            endpoint_host=endpoint_host,
            status="unavailable",
            checked_at=checked_at,
            duration_seconds=time.monotonic() - started,
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
        )


def _provider_error(response: httpx.Response) -> tuple[str, str]:
    code = f"http_{response.status_code}"
    message = response.reason_phrase
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code")
            raw_message = error.get("message")
            if raw_code is not None:
                code = str(raw_code)[:100]
            if isinstance(raw_message, str) and raw_message.strip():
                message = raw_message.strip()[:500]
    return code, message[:500]


__all__ = ["EndpointProbe", "probe_chat_endpoint"]
