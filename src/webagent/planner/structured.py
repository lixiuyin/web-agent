"""Provider-level structured planning helpers.

This module is deliberately transport-agnostic.  It builds OpenAI-compatible
function-tool and JSON-Schema payload fragments and parses both current
``tool_calls`` responses and the legacy singular ``function_call`` shape.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from webagent.core.models import ToolCall
from webagent.planner.base import TRANSPORT_AGNOSTIC_PLANNING_RULES
from webagent.tools.registry import ToolSpec

type PlannerOutputMode = Literal["auto", "native-tools", "json-schema", "prompt-json"]

NATIVE_TOOL_SYSTEM_PROMPT = f"""You are a web automation planner.

Choose exactly one of the provided tools and call it. Base the call only on the
task, current page, policy notice, and recorded evidence. Never invent a URL,
date, page result, or local path. Call `done` only when every requested field is
answered from observed evidence. If an action just failed, change the relevant
argument or strategy instead of repeating it unchanged.

{TRANSPORT_AGNOSTIC_PLANNING_RULES}
"""

JSON_SCHEMA_SYSTEM_PROMPT = f"""You are a web automation planner.

Return exactly one action matching the provider-enforced JSON Schema. Use only a
listed tool. Base the action only on the task, current page, policy notice, and
recorded evidence. Never invent a URL, date, page result, or local path. Call
`done` only when every requested field is answered from observed evidence.

{TRANSPORT_AGNOSTIC_PLANNING_RULES}
"""


def normalize_output_mode(value: str) -> PlannerOutputMode:
    """Normalize public aliases while rejecting silent configuration mistakes."""
    normalized = value.strip().casefold().replace("_", "-")
    aliases = {
        "native": "native-tools",
        "tools": "native-tools",
        "tool-calling": "native-tools",
        "provider-json-schema": "json-schema",
        "json": "json-schema",
        "legacy": "prompt-json",
        "prompt": "prompt-json",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"auto", "native-tools", "json-schema", "prompt-json"}:
        raise ValueError(f"Unsupported planner output mode: {value!r}")
    return normalized  # type: ignore[return-value]


def openai_function_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Convert provider-neutral specs to Chat Completions ``tools`` entries."""
    return [spec.as_openai_tool() for spec in specs]


def action_json_schema(specs: Sequence[ToolSpec]) -> dict[str, Any]:
    """Build a compact provider response schema for the legacy action envelope.

    Per-tool parameter schemas are enforced by native function calling and once
    again by the runtime tool validators.  The JSON-Schema fallback keeps the
    envelope compact and constrains the selected name to the exposed catalog.
    A giant conditional ``oneOf`` is intentionally avoided because several
    OpenAI-compatible providers reject or truncate schemas of that size.
    """
    names = [spec.name for spec in specs]
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": names},
            "parameters": {"type": "object", "additionalProperties": True},
            "reasoning": {"type": "string"},
        },
        "required": ["tool", "parameters", "reasoning"],
        "additionalProperties": False,
    }


def openai_response_format(specs: Sequence[ToolSpec]) -> dict[str, Any]:
    """Return the Chat Completions provider JSON-Schema response format."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "webagent_action",
            # The compact fallback deliberately leaves the per-tool parameter
            # object open; strict per-parameter enforcement lives in native
            # function schemas and runtime validation.
            "strict": False,
            "schema": action_json_schema(specs),
        },
    }


def parse_provider_tool_call(data: dict[str, Any]) -> ToolCall | None:
    """Parse exactly one native function call from an OpenAI-compatible response.

    Multiple calls are rejected: this agent executes a sequential observe-act
    loop, so accepting the first of several calls would silently discard model
    intent and could make side effects ambiguous.
    """
    message = _first_message(data)
    if message is None:
        return None

    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        if len(raw_calls) != 1 or not isinstance(raw_calls[0], dict):
            return None
        function = raw_calls[0].get("function")
        return _tool_call_from_function(function, message)

    # Older OpenAI-compatible servers use a singular function_call field.
    if "function_call" in message:
        return _tool_call_from_function(message.get("function_call"), message)
    return None


def response_text(data: dict[str, Any]) -> str:
    """Extract assistant text across the response shapes already supported here."""
    message = _first_message(data)
    if message is not None:
        content = message.get("content") or ""
        if not content:
            content = message.get("reasoning_content") or message.get("reasoning") or ""
        return content if isinstance(content, str) else ""
    response = data.get("response", "")
    if not response and isinstance(data.get("data"), dict):
        response = data["data"].get("content", "")
    return response if isinstance(response, str) else ""


def _first_message(data: dict[str, Any]) -> dict[str, Any] | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    return message if isinstance(message, dict) else None


def _tool_call_from_function(function: Any, message: dict[str, Any]) -> ToolCall | None:
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = _parse_arguments(function.get("arguments", {}))
    if arguments is None:
        return None
    content = message.get("content")
    reasoning = content.strip() if isinstance(content, str) else ""
    return ToolCall(tool_name=name.strip(), parameters=arguments, reasoning=reasoning)


def _parse_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


__all__ = [
    "JSON_SCHEMA_SYSTEM_PROMPT",
    "NATIVE_TOOL_SYSTEM_PROMPT",
    "PlannerOutputMode",
    "action_json_schema",
    "normalize_output_mode",
    "openai_function_tools",
    "openai_response_format",
    "parse_provider_tool_call",
    "response_text",
]
