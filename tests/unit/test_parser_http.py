"""Tests for the parser HTTP helpers (client construction + status mapping)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from webagent.parser._errors import FailureReason, ParserProviderError
from webagent.parser._http import build_client, raise_for_status


class TestBuildClient:
    def test_env_proxies_when_no_proxy(self):
        client = build_client(timeout=10.0)
        assert client.trust_env is True

    def test_direct_sentinel_disables_env(self):
        client = build_client(timeout=10.0, proxy="direct")
        assert client.trust_env is False

    def test_explicit_proxy_disables_env(self):
        client = build_client(timeout=10.0, proxy="socks5://127.0.0.1:7897")
        assert client.trust_env is False


class TestRaiseForStatus:
    def _resp(self, code: int, text: str = "body"):
        return SimpleNamespace(status_code=code, text=text)

    def test_2xx_is_noop(self):
        raise_for_status(self._resp(200), "marker", "ctx")

    def test_auth_failure_not_retryable(self):
        with pytest.raises(ParserProviderError) as exc:
            raise_for_status(self._resp(401), "marker", "ctx")
        assert exc.value.reason == FailureReason.AUTH_FAILED
        assert exc.value.retryable is False

    def test_rate_limited(self):
        with pytest.raises(ParserProviderError) as exc:
            raise_for_status(self._resp(429), "mineru", "ctx")
        assert exc.value.reason == FailureReason.RATE_LIMITED

    def test_server_error_retryable(self):
        with pytest.raises(ParserProviderError) as exc:
            raise_for_status(self._resp(503), "paddle", "ctx")
        assert exc.value.retryable is True

    def test_other_4xx_not_retryable(self):
        with pytest.raises(ParserProviderError) as exc:
            raise_for_status(self._resp(404), "paddle", "ctx")
        assert exc.value.retryable is False
