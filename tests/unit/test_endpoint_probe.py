"""Provider endpoint preflight tests."""

from __future__ import annotations

import json

import httpx

from webagent.evaluation.endpoints import probe_chat_endpoint


def _transport(status: int, payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer private-key"
        body = json.loads(request.content)
        assert body["model"] == "model-a"
        return httpx.Response(status, json=payload, request=request)

    return httpx.MockTransport(handler)


def test_endpoint_probe_accepts_nonempty_provider_choices_without_leaking_key() -> None:
    result = probe_chat_endpoint(
        api_url="https://api.example.test/v1/chat/completions",
        api_key="private-key",
        provider="test-provider",
        model="model-a",
        transport=_transport(200, {"choices": [{"message": {"content": "READY"}}]}),
    )

    assert result.status == "available"
    assert result.endpoint_host == "api.example.test"
    assert "private-key" not in result.model_dump_json()


def test_endpoint_probe_preserves_provider_policy_rejection_as_unavailable() -> None:
    result = probe_chat_endpoint(
        api_url="https://api.example.test/v1/chat/completions",
        api_key="private-key",
        provider="test-provider",
        model="model-a",
        transport=_transport(
            404,
            {
                "error": {
                    "code": 404,
                    "message": "No endpoints available matching your data policy.",
                }
            },
        ),
    )

    assert result.status == "unavailable"
    assert result.status_code == 404
    assert result.error_code == "404"
    assert result.error_message == "No endpoints available matching your data policy."


def test_endpoint_probe_rejects_malformed_success_payload() -> None:
    result = probe_chat_endpoint(
        api_url="https://api.example.test/v1/chat/completions",
        api_key="private-key",
        provider="test-provider",
        model="model-a",
        transport=_transport(200, {"choices": []}),
    )

    assert result.status == "unavailable"
    assert result.error_code == "invalid_success_payload"


def test_endpoint_probe_retries_transient_rate_limit_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"message": "rate limited"}},
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "READY"}}]},
            request=request,
        )

    result = probe_chat_endpoint(
        api_url="https://api.example.test/v1/chat/completions",
        api_key="private-key",
        provider="test-provider",
        model="model-a",
        transient_retries=2,
        retry_base_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "available"
    assert result.schema_version == 2
    assert result.attempt_count == 2
    assert result.transient_retry_count == 1
    assert result.retry_delays_seconds == [0.0]


def test_endpoint_probe_stops_after_bounded_transient_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            json={"error": {"code": "overloaded", "message": "try later"}},
            request=request,
        )

    result = probe_chat_endpoint(
        api_url="https://api.example.test/v1/chat/completions",
        api_key="private-key",
        provider="test-provider",
        model="model-a",
        transient_retries=2,
        retry_base_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert calls == 3
    assert result.status == "unavailable"
    assert result.status_code == 503
    assert result.error_code == "overloaded"
    assert result.attempt_count == 3
    assert result.transient_retry_count == 2
