"""Auditable provider-endpoint availability checks for model studies."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field


class EndpointProbe(BaseModel):
    """One credential-free record of whether a requested model can be invoked."""

    schema_version: int = 2
    provider: str
    model: str
    endpoint_host: str
    status: Literal["available", "unavailable"]
    checked_at: str
    duration_seconds: float = Field(ge=0.0)
    attempt_count: int = Field(default=1, ge=1)
    transient_retry_count: int = Field(default=0, ge=0)
    retry_delays_seconds: list[float] = Field(default_factory=list)
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class _ProbeFields(TypedDict):
    provider: str
    model: str
    endpoint_host: str
    checked_at: str
    duration_seconds: float
    attempt_count: int
    transient_retry_count: int
    retry_delays_seconds: list[float]


def probe_chat_endpoint(
    *,
    api_url: str,
    api_key: str,
    provider: str,
    model: str,
    timeout_seconds: float = 30.0,
    transient_retries: int = 2,
    retry_base_seconds: float = 0.5,
    retry_max_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> EndpointProbe:
    """Make a minimal real inference request before an expensive study run.

    HTTP errors are retained as endpoint evidence rather than converted into
    benchmark task failures.  API keys, full URLs, and response bodies are never
    written to the result.  Transient rate-limit and server failures receive a
    bounded number of retries, all of which are represented in the result.
    """
    transient_retries = max(0, transient_retries)
    retry_base_seconds = max(0.0, retry_base_seconds)
    retry_max_seconds = max(0.0, retry_max_seconds)
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
    retry_delays: list[float] = []
    attempt_count = 0
    response: httpx.Response | None = None
    last_error: httpx.HTTPError | None = None
    with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
        for attempt in range(transient_retries + 1):
            attempt_count = attempt + 1
            try:
                response = client.post(api_url, headers=headers, json=payload)
                last_error = None
            except httpx.HTTPError as exc:
                response = None
                last_error = exc
            transient_response = response is not None and (
                response.status_code == 429 or 500 <= response.status_code < 600
            )
            if not transient_response and last_error is None:
                break
            if attempt >= transient_retries:
                break
            delay = _retry_delay_seconds(
                response=response,
                attempt=attempt,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
            retry_delays.append(delay)
            time.sleep(delay)

    common: _ProbeFields = {
        "provider": provider,
        "model": model,
        "endpoint_host": endpoint_host,
        "checked_at": checked_at,
        "duration_seconds": time.monotonic() - started,
        "attempt_count": attempt_count,
        "transient_retry_count": len(retry_delays),
        "retry_delays_seconds": retry_delays,
    }
    if last_error is not None:
        return EndpointProbe(
            **common,
            status="unavailable",
            error_code=type(last_error).__name__,
            error_message=str(last_error)[:500],
        )
    assert response is not None
    if response.is_success:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("choices"), list) and body["choices"]:
            return EndpointProbe(
                **common,
                status="available",
                status_code=response.status_code,
            )
        return EndpointProbe(
            **common,
            status="unavailable",
            status_code=response.status_code,
            error_code="invalid_success_payload",
            error_message="successful response did not contain a non-empty choices array",
        )
    code, message = _provider_error(response)
    return EndpointProbe(
        **common,
        status="unavailable",
        status_code=response.status_code,
        error_code=code,
        error_message=message,
    )


def _retry_delay_seconds(
    *,
    response: httpx.Response | None,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
) -> float:
    retry_after = _retry_after_seconds(response) if response is not None else None
    delay = retry_after if retry_after is not None else base_seconds * (2**attempt)
    return min(max_seconds, max(0.0, delay))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


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
