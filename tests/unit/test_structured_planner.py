"""Provider-native planner output and fallback behavior."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from webagent.core.models import BrowserState
from webagent.planner.api import APIPlanner
from webagent.planner.structured import (
    JSON_SCHEMA_SYSTEM_PROMPT,
    NATIVE_TOOL_SYSTEM_PROMPT,
    action_json_schema,
    normalize_output_mode,
    openai_function_tools,
    parse_provider_tool_call,
)
from webagent.tools.registry import ToolSpec


def _spec(name: str, required: tuple[str, ...] = ()) -> ToolSpec:
    properties = {field: {"type": "string"} for field in required}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return ToolSpec(name=name, description=f"Use {name}", parameters=schema)


def _state() -> BrowserState:
    return BrowserState(
        dom_summary="<button>Go</button>",
        url="https://example.test",
        title="Example",
        timestamp="2026-01-01T00:00:00Z",
    )


def _planner(mode: str) -> APIPlanner:
    planner = APIPlanner(
        api_url="https://provider.test/v1/chat/completions",
        api_key="k",
        model_name="model",
        output_mode=mode,
    )
    planner._supports_vision = False
    planner.configure_tools([_spec("goto", ("url",)), _spec("done", ("summary",))])
    return planner


def _native_response(name: str, arguments: str, *, finish_reason: str = "tool_calls") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }


def _unsupported(field: str, status: int = 400) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    response = httpx.Response(status, request=request, text=f"Unsupported parameter: {field}")
    return httpx.HTTPStatusError("unsupported", request=request, response=response)


def test_mode_aliases_are_explicit_and_validated() -> None:
    assert normalize_output_mode("tool_calling") == "native-tools"
    assert normalize_output_mode("provider-json-schema") == "json-schema"
    assert normalize_output_mode("legacy") == "prompt-json"
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_output_mode("magic")


def test_provider_transports_preserve_task_semantic_rules() -> None:
    for prompt in (NATIVE_TOOL_SYSTEM_PROMPT, JSON_SCHEMA_SYSTEM_PROMPT):
        assert "at least two differently worded searches" in prompt
        assert "first-party provenance" in prompt
        assert "selected winner's explicit date" in prompt
        assert "pdf_analyze_figure" in prompt
        assert "highlighted bar" in prompt
    assert "Return exactly one action" not in NATIVE_TOOL_SYSTEM_PROMPT


def test_openai_tools_and_action_schema_use_exposed_catalog() -> None:
    specs = [_spec("goto", ("url",)), _spec("done", ("summary",))]
    tools = openai_function_tools(specs)
    schema = action_json_schema(specs)

    assert [item["function"]["name"] for item in tools] == ["goto", "done"]
    assert tools[0]["function"]["parameters"]["required"] == ["url"]
    assert schema["properties"]["tool"]["enum"] == ["goto", "done"]


def test_parse_current_and_legacy_provider_function_calls() -> None:
    current = parse_provider_tool_call(_native_response("goto", '{"url":"https://x"}'))
    legacy = parse_provider_tool_call(
        {
            "choices": [
                {
                    "message": {
                        "content": "navigate",
                        "function_call": {
                            "name": "goto",
                            "arguments": {"url": "https://x"},
                        },
                    }
                }
            ]
        }
    )

    assert current is not None and current.parameters == {"url": "https://x"}
    assert legacy is not None and legacy.reasoning == "navigate"


@pytest.mark.parametrize(
    "data",
    [
        {"choices": [{"message": {"tool_calls": []}}]},
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "a", "arguments": "{}"}},
                            {"function": {"name": "b", "arguments": "{}"}},
                        ]
                    }
                }
            ]
        },
        _native_response("goto", "not-json"),
        _native_response("goto", "[]"),
    ],
)
def test_parse_provider_call_rejects_ambiguous_or_malformed_calls(data: dict) -> None:
    assert parse_provider_tool_call(data) is None


async def test_native_mode_sends_provider_function_tools() -> None:
    planner = _planner("native-tools")
    payloads: list[dict[str, Any]] = []

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del timeout
        payloads.append(payload)
        return _native_response("goto", '{"url":"https://example.test/next"}')

    planner._post_data = post_data  # type: ignore[method-assign]
    call = await planner.plan_action("open next", _state(), "", "goto: nav\ndone: finish")

    assert call is not None and call.tool_name == "goto"
    assert payloads[0]["tool_choice"] == "required"
    assert payloads[0]["parallel_tool_calls"] is False
    assert [tool["function"]["name"] for tool in payloads[0]["tools"]] == ["goto", "done"]
    assert "response_format" not in payloads[0]
    assert planner.last_call_metadata["effective_output_mode"] == "native-tools"


async def test_json_schema_mode_sends_provider_response_format() -> None:
    planner = _planner("json-schema")
    payloads: list[dict[str, Any]] = []

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del timeout
        payloads.append(payload)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": (
                            '{"tool":"done","parameters":{"summary":"ok"},"reasoning":"complete"}'
                        )
                    },
                }
            ]
        }

    planner._post_data = post_data  # type: ignore[method-assign]
    call = await planner.plan_action("finish", _state(), "", "goto, done")

    assert call is not None and call.tool_name == "done"
    response_format = payloads[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"]["properties"]["tool"]["enum"] == [
        "goto",
        "done",
    ]
    assert "tools" not in payloads[0]


async def test_auto_falls_back_only_after_explicit_capability_error() -> None:
    planner = _planner("auto")
    payloads: list[dict[str, Any]] = []

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del timeout
        payloads.append(payload)
        if "tools" in payload:
            raise _unsupported("tools")
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"tool":"done","parameters":{"summary":"ok"},"reasoning":"complete"}'
                        )
                    }
                }
            ]
        }

    planner._post_data = post_data  # type: ignore[method-assign]
    call = await planner.plan_action("finish", _state(), "", "goto, done")

    assert call is not None and call.tool_name == "done"
    assert len(payloads) == 2
    assert "tools" in payloads[0]
    assert "response_format" in payloads[1]
    assert planner.effective_output_mode == "json-schema"
    assert planner.structured_fallbacks[0]["from"] == "native-tools"


async def test_auto_does_not_mask_auth_or_operational_errors() -> None:
    planner = _planner("auto")

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del payload, timeout
        raise _unsupported("tools", status=401)

    planner._post_data = post_data  # type: ignore[method-assign]
    with pytest.raises(httpx.HTTPStatusError):
        await planner.plan_action("finish", _state(), "", "goto, done")
    assert planner.structured_fallbacks == []


@pytest.mark.parametrize(
    "body",
    [
        "Invalid schema for function tool done: required mismatch",
        "Unknown model; tools are unavailable for this request",
        "Invalid request body: tools must be an array",
        "Schema validation failed for response_format",
    ],
)
async def test_auto_does_not_downgrade_schema_model_or_request_defects(body: str) -> None:
    planner = _planner("auto")

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del payload, timeout
        request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
        response = httpx.Response(400, request=request, text=body)
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    planner._post_data = post_data  # type: ignore[method-assign]
    with pytest.raises(httpx.HTTPStatusError):
        await planner.plan_action("finish", _state(), "", "goto, done")
    assert planner.structured_fallbacks == []


async def test_auto_can_fall_back_through_both_provider_features_and_sticks() -> None:
    planner = _planner("auto")
    structured_calls = 0
    prompt_calls = 0

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        nonlocal structured_calls
        del timeout
        structured_calls += 1
        if "tools" in payload:
            raise _unsupported("tool_choice")
        raise _unsupported("response_format")

    async def post(payload: dict[str, Any], timeout: int | None = None) -> str:
        nonlocal prompt_calls
        del payload, timeout
        prompt_calls += 1
        return '{"tool":"done","parameters":{"summary":"ok"}}'

    planner._post_data = post_data  # type: ignore[method-assign]
    planner._post = post  # type: ignore[method-assign]

    first = await planner.plan_action("finish", _state(), "", "goto, done")
    second = await planner.plan_action("finish", _state(), "", "goto, done")

    assert first is not None and second is not None
    assert structured_calls == 2
    assert prompt_calls == 2
    assert planner.effective_output_mode == "prompt-json"
    assert [item["from"] for item in planner.structured_fallbacks] == [
        "native-tools",
        "json-schema",
    ]


async def test_explicit_native_mode_never_silently_downgrades() -> None:
    planner = _planner("native-tools")

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del payload, timeout
        raise _unsupported("tools")

    planner._post_data = post_data  # type: ignore[method-assign]
    with pytest.raises(httpx.HTTPStatusError):
        await planner.plan_action("finish", _state(), "", "goto, done")
    assert planner.structured_fallbacks == []


async def test_explicit_structured_mode_requires_bound_specs() -> None:
    planner = APIPlanner(
        api_url="https://provider.test/v1/chat/completions",
        api_key="k",
        output_mode="native-tools",
    )
    planner._supports_vision = False

    with pytest.raises(RuntimeError, match="configure_tools"):
        await planner.plan_action("task", _state(), "", "done")


async def test_native_mode_rejects_unexposed_tool_name() -> None:
    planner = _planner("native-tools")

    async def post_data(payload: dict[str, Any], timeout: int | None = None) -> dict:
        del payload, timeout
        return _native_response("secret_tool", "{}")

    planner._post_data = post_data  # type: ignore[method-assign]
    assert await planner.plan_action("task", _state(), "", "goto, done") is None
