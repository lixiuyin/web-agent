"""Risk classification and executor approval-gate tests."""

from __future__ import annotations

from typing import Any

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry
from webagent.tools.risk import (
    ActionRiskPolicy,
    BrowserRiskContext,
    RiskAssessment,
    assess_tool_call,
)


class _Tool:
    _tool_name = "click"
    _tool_description = "click"

    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        del params
        return ToolResult(success=True, tool_name="click")


def _purchase_call() -> ToolCall:
    return ToolCall(
        tool_name="click",
        parameters={"selector": {"type": "css", "value": "#place-order"}},
    )


def test_risk_classifier_distinguishes_read_sensitive_and_external_actions() -> None:
    assert assess_tool_call(ToolCall(tool_name="extract_text")).level == "low"
    assert (
        assess_tool_call(
            ToolCall(
                tool_name="type",
                parameters={"selector": {"type": "css", "value": "#password"}},
            )
        ).level
        == "medium"
    )
    assert assess_tool_call(_purchase_call()).level == "high"
    assert assess_tool_call(ToolCall(tool_name="upload_file")).approval_required is True
    assert (
        assess_tool_call(
            ToolCall(
                tool_name="frame_interact",
                parameters={"action": "type", "selector": "#password", "text": "secret"},
            )
        ).external_effect
        == "sensitive_input"
    )


async def test_default_risk_policy_denies_high_risk_action() -> None:
    policy = ActionRiskPolicy()
    decision = await policy.authorize(_purchase_call())
    assert decision.allowed is False
    assert decision.assessment.external_effect == "external_state_change"
    assert "denied" in policy.prompt_notice


async def test_prompt_policy_records_real_callback_approval() -> None:
    seen: list[RiskAssessment] = []

    async def approve(_call: ToolCall, assessment: RiskAssessment) -> bool:
        seen.append(assessment)
        return True

    decision = await ActionRiskPolicy("prompt", confirmer=approve).authorize(_purchase_call())
    assert decision.allowed is True
    assert decision.approval_received is True
    assert seen[0].level == "high"


async def test_executor_denies_before_external_tool_and_audits_risk() -> None:
    registry = ToolRegistry()
    registry.register(_Tool())
    result = await ToolExecutor(registry, risk_policy=ActionRiskPolicy()).execute(_purchase_call())
    assert result.success is False
    assert result.audit["risk"]["decision"] == "deny"
    assert result.audit["risk"]["approval_required"] is True


async def test_explicit_allow_mode_is_audited() -> None:
    registry = ToolRegistry()
    registry.register(_Tool())
    result = await ToolExecutor(registry, risk_policy=ActionRiskPolicy("allow")).execute(
        _purchase_call()
    )
    assert result.success is True
    assert result.audit["risk"]["approval_received"] is True


async def test_explicit_allow_can_be_confined_to_sandbox_origins() -> None:
    class _Context:
        def __init__(self, origin: str) -> None:
            self.origin = origin

        async def describe_action(self, _call: ToolCall) -> str:
            return "{'text': 'Place order', 'formMethod': 'post'}"

        async def action_origin(self) -> str:
            return self.origin

    trusted = ActionRiskPolicy(
        "allow",
        context_provider=_Context("http://127.0.0.1:8123/checkout"),
        trusted_origins={"http://127.0.0.1:8123"},
    )
    public = ActionRiskPolicy(
        "allow",
        context_provider=_Context("https://shop.example/checkout"),
        trusted_origins={"http://127.0.0.1:8123"},
    )

    assert (await trusted.authorize(_purchase_call())).allowed is True
    denied = await public.authorize(_purchase_call())
    assert denied.allowed is False
    assert "outside" in denied.reason


async def test_trusted_origin_mode_fails_closed_without_origin_provider() -> None:
    class _Context:
        async def describe_action(self, _call: ToolCall) -> str:
            return "{'text': 'Place order', 'formMethod': 'post'}"

    decision = await ActionRiskPolicy(
        "allow",
        context_provider=_Context(),
        trusted_origins={"http://127.0.0.1:8123"},
    ).authorize(_purchase_call())

    assert decision.allowed is False


async def test_executor_fails_closed_when_confirmation_callback_errors() -> None:
    async def broken(_call: ToolCall, _assessment: RiskAssessment) -> bool:
        raise EOFError("no terminal")

    registry = ToolRegistry()
    registry.register(_Tool())
    result = await ToolExecutor(
        registry,
        risk_policy=ActionRiskPolicy("prompt", confirmer=broken),
    ).execute(_purchase_call())

    assert result.success is False
    assert result.audit["risk"]["decision"] == "deny"
    assert "failed closed" in str(result.error)


async def test_context_provider_can_expose_opaque_high_risk_button() -> None:
    class _Context:
        async def describe_action(self, _call: ToolCall) -> str:
            return "{'tag': 'BUTTON', 'text': 'Place order', 'formMethod': 'post'}"

    call = ToolCall(
        tool_name="click",
        parameters={"selector": {"type": "css", "value": ".primary"}},
    )
    decision = await ActionRiskPolicy(context_provider=_Context()).authorize(call)

    assert decision.allowed is False
    assert decision.assessment.external_effect == "external_state_change"


def test_post_form_context_is_high_risk_even_with_generic_button_text() -> None:
    assessment = assess_tool_call(
        ToolCall(tool_name="click", parameters={"selector": ".primary"}),
        context="{'text': 'Continue', 'formMethod': 'post'}",
    )

    assert assessment.level == "high"


async def test_browser_risk_context_reads_css_and_text_target_metadata() -> None:
    class _Locator:
        first: _Locator

        def __init__(self) -> None:
            self.first = self

        async def evaluate(self, _script: str, *, timeout: int) -> dict[str, str]:
            assert timeout == 1000
            return {"text": "Place order", "formMethod": "post"}

    class _Page:
        def __init__(self) -> None:
            self.selected: list[tuple[str, str]] = []

        def locator(self, value: str) -> _Locator:
            self.selected.append(("css", value))
            return _Locator()

        def get_by_text(self, value: str, *, exact: bool) -> _Locator:
            assert exact is False
            self.selected.append(("text", value))
            return _Locator()

    page = _Page()
    browser = type("Browser", (), {"page": page})()
    provider = BrowserRiskContext(browser)

    css = await provider.describe_action(
        ToolCall(
            tool_name="click",
            parameters={"selector": {"type": "css", "value": ".primary"}},
        )
    )
    text = await provider.describe_action(
        ToolCall(
            tool_name="press",
            parameters={"selector": {"type": "text", "value": "Continue"}},
        )
    )

    assert "Place order" in css and "post" in text
    assert page.selected == [("css", ".primary"), ("text", "Continue")]


async def test_browser_risk_context_fails_closed_to_empty_metadata() -> None:
    class _BrokenPage:
        def locator(self, _value: str) -> Any:
            raise RuntimeError("detached")

    browser = type("Browser", (), {"page": _BrokenPage()})()
    provider = BrowserRiskContext(browser)

    assert await provider.describe_action(ToolCall(tool_name="extract_text")) == ""
    assert (
        await provider.describe_action(
            ToolCall(tool_name="click", parameters={"selector": "not-structured"})
        )
        == ""
    )
    assert (
        await provider.describe_action(
            ToolCall(
                tool_name="click",
                parameters={"selector": {"type": "xpath", "value": "//button"}},
            )
        )
        == ""
    )
    assert (
        await provider.describe_action(
            ToolCall(
                tool_name="click",
                parameters={"selector": {"type": "css", "value": ".missing"}},
            )
        )
        == ""
    )


def test_risk_prompt_notices_cover_all_modes() -> None:
    assert "explicitly allowed" in ActionRiskPolicy("allow").prompt_notice
    assert "human confirmation" in ActionRiskPolicy("prompt").prompt_notice
