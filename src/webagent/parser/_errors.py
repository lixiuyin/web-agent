"""Error types for the cloud-native parser cascade."""

from __future__ import annotations

import enum

# Retries per provider for retryable failures (1 initial attempt + MAX_RETRIES
# retries). Transient ConnectError/5xx through a flaky proxy usually clears on a
# second or third try, so the cascade shouldn't demote to the next provider too
# eagerly.
MAX_RETRIES = 2


class FailureReason(enum.StrEnum):
    """Categorised reason for a parser failure."""

    QUALITY_GATE = "quality_gate"
    NETWORK_TIMEOUT = "network_timeout"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"


class ParserProviderError(Exception):
    """Raised when a single parser provider fails.

    ``retryable=True`` errors trigger an immediate retry of the same provider
    before the cascade falls back to the next one.
    """

    def __init__(
        self,
        provider: str,
        cause: Exception | None = None,
        retryable: bool = True,
        reason: FailureReason = FailureReason.UNKNOWN,
    ) -> None:
        self.provider = provider
        self.cause = cause
        self.retryable = retryable
        self.reason = reason
        msg = f"{provider} failed ({reason.value})"
        if cause is not None:
            # Some exceptions (e.g. httpx.ConnectError) stringify to "" — fall back
            # to the type name so failures stay diagnosable.
            detail = str(cause).strip() or type(cause).__name__
            msg += f": {detail}"
        super().__init__(msg)


class AllParsersFailedError(Exception):
    """Raised when every parser in the cascade has failed."""

    def __init__(self, errors: list[ParserProviderError]) -> None:
        self.errors = errors
        details = "; ".join(
            f"{e.provider} ({'retryable' if e.retryable else 'permanent'}, {e.reason.value})"
            for e in errors
        )
        super().__init__(f"All parsers failed: {details}")
