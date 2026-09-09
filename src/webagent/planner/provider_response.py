"""Provider response cleanup and explicit capability-error classification."""

from __future__ import annotations

import re

import httpx

from webagent.planner.structured import PlannerOutputMode

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> reasoning chains from model responses.

    Reasoning models (DeepSeek-R1, GLM-Z1, QwQ, MiniMax-M2.7, etc.) may inline their
    chain-of-thought inside <think>...</think> tags before the actual answer.
    Some models omit the closing </think> tag — we handle that too.
    """
    # First strip properly closed tags
    stripped = _THINK_TAG_RE.sub("", text).strip()
    # Handle unclosed <think> tags (everything from <think> to end)
    if re.search(r"<think>", stripped, re.IGNORECASE):
        stripped = _THINK_UNCLOSED_RE.sub("", stripped).strip()
    # An empty final channel is a retryable failure, never a reasoning answer.
    return stripped


def _structured_output_unsupported(exc: httpx.HTTPStatusError, mode: PlannerOutputMode) -> bool:
    """Only downgrade on explicit client-side feature incompatibility.

    Authentication, rate limits, timeouts, and server errors must propagate;
    treating them as capability failures would hide operational incidents.
    """
    response = exc.response
    if response.status_code not in {400, 404, 415, 422}:
        return False
    body = response.text.casefold()
    # Schema/request/model errors are implementation or configuration defects,
    # not evidence that the provider lacks structured-output support.
    non_capability_errors = (
        "invalid schema",
        "schema validation",
        "invalid function",
        "invalid tool definition",
        "unknown model",
        "model not found",
        "invalid request body",
        "malformed request",
        "invalid json",
        "missing required",
        "required field",
    )
    if any(term in body for term in non_capability_errors):
        return False
    explicit_capability_errors = (
        "unsupported",
        "not supported",
        "does not support",
        "unsupported parameter",
        "unknown parameter",
        "unrecognized parameter",
        "unexpected parameter",
        "extra fields not permitted",
    )
    feature_terms = (
        ("tools", "tool_choice", "function", "parallel_tool_calls")
        if mode == "native-tools"
        else ("response_format", "json_schema", "json schema")
    )
    return any(term in body for term in explicit_capability_errors) and any(
        term in body for term in feature_terms
    )


def _required_tool_choice_unsupported(exc: httpx.HTTPStatusError) -> bool:
    """Detect providers that support tools but reject forced tool selection."""
    response = exc.response
    if response.status_code not in {400, 404, 415, 422}:
        return False
    body = response.text.casefold()
    return "tool_choice" in body and any(
        phrase in body
        for phrase in (
            "does not support required",
            "doesn't support required",
            "required or object",
            "required is not supported",
            "only supports auto",
        )
    )
