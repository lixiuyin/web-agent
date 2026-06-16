"""HTTP utilities for cloud parser providers.

web-agent runs as a single-process CLI and parses PDFs infrequently, so the
cascade creates one short-lived ``httpx.AsyncClient`` per parse (via ``async
with``) and passes it to each provider.  No global client lifecycle to manage.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ._errors import FailureReason, ParserProviderError

logger = logging.getLogger(__name__)


# Sentinel values for ``proxy`` that mean "force a direct connection, ignoring
# any HTTP(S)_PROXY/ALL_PROXY env vars".  Useful when a system proxy reaches some
# provider hosts but black-holes others (e.g. a Clash rule that routes
# mineru.net but not its result CDN cdn-mineru.openxlab.org.cn).
_DIRECT_SENTINELS = frozenset({"direct", "none", "off", "no"})


def build_client(timeout: float, proxy: str | None = None) -> httpx.AsyncClient:
    """Create an AsyncClient with sane timeouts and connection limits.

    ``proxy`` controls routing for the cloud OCR calls:
      - falsy/empty  → use the system ``HTTP(S)_PROXY`` / ``ALL_PROXY`` env vars
        (``trust_env`` stays on), so existing setups keep working.
      - ``"direct"`` (or none/off/no) → ignore env proxies, connect directly.
      - a URL (e.g. ``socks5://127.0.0.1:7897``) → pin that proxy explicitly.
    """
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "limits": httpx.Limits(max_keepalive_connections=5, max_connections=10),
    }
    if proxy:
        if proxy.strip().lower() in _DIRECT_SENTINELS:
            kwargs["trust_env"] = False  # ignore env proxies → direct connection
        else:
            kwargs["proxy"] = proxy
            kwargs["trust_env"] = False  # use ONLY the pinned proxy
    return httpx.AsyncClient(**kwargs)


def raise_for_status(resp: Any, provider: str, context: str) -> None:
    """Translate an HTTP error response into a ``ParserProviderError``.

    No-op for 2xx/3xx responses.
    """
    code = resp.status_code
    if code < 400:
        return
    if code in (401, 403):
        logger.error("%s API key invalid/revoked (HTTP %d) at %s", provider, code, context)
        raise ParserProviderError(
            provider=provider,
            retryable=False,
            reason=FailureReason.AUTH_FAILED,
            cause=Exception(f"API key invalid/revoked (HTTP {code}) at {context}"),
        )
    if code == 429:
        raise ParserProviderError(
            provider=provider,
            retryable=False,
            reason=FailureReason.RATE_LIMITED,
            cause=Exception(f"rate limited (HTTP 429) at {context}"),
        )
    if code >= 500:
        raise ParserProviderError(
            provider=provider,
            retryable=True,
            cause=Exception(f"server error {code} at {context}"),
        )
    logger.debug("Provider %s HTTP %d at %s: %s", provider, code, context, resp.text[:500])
    raise ParserProviderError(
        provider=provider,
        retryable=False,
        cause=Exception(f"HTTP {code} from {provider} at {context}"),
    )
