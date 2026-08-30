"""Risk classification and human approval for externally consequential actions."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from webagent.core.models import ToolCall

RiskLevel = Literal["low", "medium", "high"]
ApprovalMode = Literal["deny", "prompt", "allow"]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    external_effect: str
    approval_required: bool

    def as_audit(self, *, approval_received: bool) -> dict[str, object]:
        return {
            "risk_level": self.level,
            "external_effect": self.external_effect,
            "approval_required": self.approval_required,
            "approval_received": approval_received,
        }


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    assessment: RiskAssessment
    approval_received: bool
    reason: str

    def as_audit(self) -> dict[str, object]:
        return {
            "decision": "allow" if self.allowed else "deny",
            "reason": self.reason,
            **self.assessment.as_audit(approval_received=self.approval_received),
        }


ConfirmationCallback = Callable[[ToolCall, RiskAssessment], Awaitable[bool]]

_HIGH_RISK_ACTION_RE = re.compile(
    r"(?:buy|purchase|place[-_ ]?order|pay|checkout|delete|remove[-_ ]?account|"
    r"send|publish|post|submit[-_ ]?(?:application|order)|apply|book|confirm[-_ ]?booking|"
    r"save[-_ ]?(?:profile|settings)|update[-_ ]?(?:profile|account)|log[-_ ]?in|"
    r"sign[-_ ]?(?:in|up)|register|follow|unfollow|"
    r"unsubscribe|transfer|withdraw|accept[-_ ]?(?:terms|offer)|购买|付款|下单|删除|发送|"
    r"发布|提交|申请|预订|确认)",
    flags=re.IGNORECASE,
)
_SENSITIVE_INPUT_RE = re.compile(
    r"(?:password|passwd|passcode|credit|card|cvv|cvc|ssn|passport|token|secret|"
    r"密码|信用卡|护照|验证码)",
    flags=re.IGNORECASE,
)
_STATE_CHANGING_CONTEXT_RE = re.compile(r"formmethod['\" :]+post", flags=re.IGNORECASE)


def assess_tool_call(tool_call: ToolCall, *, context: str = "") -> RiskAssessment:
    """Classify likely side effects from tool name and planner-visible parameters."""
    name = tool_call.tool_name.casefold()
    searchable = f"{name} {tool_call.parameters!s} {context}"
    if name == "upload_file":
        return RiskAssessment("high", "local_file_disclosure", True)
    if name in {"click", "click_link", "press", "frame_interact", "shadow_dom"} and (
        _HIGH_RISK_ACTION_RE.search(searchable) or _STATE_CHANGING_CONTEXT_RE.search(context)
    ):
        return RiskAssessment("high", "external_state_change", True)
    is_typing = name == "type" or (
        name in {"frame_interact", "shadow_dom"}
        and str(tool_call.parameters.get("action", "")).casefold() == "type"
    )
    if is_typing and _SENSITIVE_INPUT_RE.search(searchable):
        return RiskAssessment("medium", "sensitive_input", False)
    return RiskAssessment("low", "none_or_reversible", False)


class ActionRiskPolicy:
    """Fail closed for high-risk actions unless explicitly approved."""

    def __init__(
        self,
        mode: ApprovalMode = "deny",
        *,
        confirmer: ConfirmationCallback | None = None,
        context_provider: ActionContextProvider | None = None,
        trusted_origins: Collection[str] | None = None,
    ) -> None:
        self._mode = mode
        self._confirmer = confirmer
        self._context_provider = context_provider
        self._trusted_origins = (
            frozenset(
                origin
                for value in trusted_origins
                if (origin := _normalized_origin(value)) is not None
            )
            if trusted_origins is not None
            else None
        )

    @property
    def prompt_notice(self) -> str:
        if self._mode == "allow":
            return "RISK POLICY: High-risk actions are explicitly allowed for this trusted run."
        if self._mode == "prompt":
            return "RISK POLICY: High-risk actions pause for explicit human confirmation."
        return (
            "RISK POLICY: High-risk external actions are denied. Do not claim completion if "
            "a required consequential action is blocked."
        )

    async def authorize(self, tool_call: ToolCall) -> RiskDecision:
        context = (
            await self._context_provider.describe_action(tool_call)
            if self._context_provider is not None
            else ""
        )
        assessment = assess_tool_call(tool_call, context=context)
        if not assessment.approval_required:
            return RiskDecision(True, assessment, False, "action does not require approval")
        if self._mode == "allow":
            if self._trusted_origins is not None:
                origin = await self._action_origin()
                if origin not in self._trusted_origins:
                    return RiskDecision(
                        False,
                        assessment,
                        False,
                        "high-risk action is outside the explicitly trusted sandbox origins",
                    )
            return RiskDecision(True, assessment, True, "high-risk actions explicitly allowed")
        if self._mode == "prompt" and self._confirmer is not None:
            approved = await self._confirmer(tool_call, assessment)
            return RiskDecision(
                approved,
                assessment,
                approved,
                "human approved action" if approved else "human denied action",
            )
        return RiskDecision(False, assessment, False, "high-risk action requires human approval")

    async def _action_origin(self) -> str | None:
        """Return the active action origin when the context provider exposes it."""
        if self._context_provider is None:
            return None
        provider = getattr(self._context_provider, "action_origin", None)
        if not callable(provider):
            return None
        value = await provider()
        return _normalized_origin(value) if isinstance(value, str) else None


class ActionContextProvider(Protocol):
    async def describe_action(self, tool_call: ToolCall) -> str: ...


class BrowserRiskContext:
    """Read non-sensitive target metadata before an interaction takes effect."""

    def __init__(self, browser: Any) -> None:
        self._browser = browser

    async def action_origin(self) -> str | None:
        """Return only the active page origin, never paths, query data, or credentials."""
        value = getattr(getattr(self._browser, "page", None), "url", None)
        return _normalized_origin(value) if isinstance(value, str) else None

    async def describe_action(self, tool_call: ToolCall) -> str:
        if tool_call.tool_name.casefold() not in {
            "click",
            "frame_interact",
            "press",
            "shadow_dom",
        }:
            return ""
        selector = tool_call.parameters.get("selector")
        if not isinstance(selector, dict):
            return ""
        value = selector.get("value")
        if not isinstance(value, str) or not value:
            return ""
        try:
            if selector.get("type") == "css":
                locator = self._browser.page.locator(value).first
            elif selector.get("type") == "text":
                locator = self._browser.page.get_by_text(value, exact=False).first
            else:
                return ""
            metadata = await locator.evaluate(
                """element => ({
                    tag: element.tagName,
                    type: element.type || '',
                    text: (element.innerText || element.value || '').slice(0, 200),
                    aria: element.getAttribute('aria-label') || '',
                    title: element.getAttribute('title') || '',
                    name: element.getAttribute('name') || '',
                    id: element.id || '',
                    href: element.href || '',
                    formMethod: element.form ? element.form.method : '',
                    formAction: element.form ? element.form.action : ''
                })""",
                timeout=1000,
            )
        except Exception:
            return ""
        return str(metadata) if isinstance(metadata, dict) else ""


def _normalized_origin(value: str) -> str | None:
    """Normalize an HTTP(S) URL to a same-origin comparison key."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.casefold()
    default_port = 80 if parsed.scheme.casefold() == "http" else 443
    try:
        port = parsed.port or default_port
    except ValueError:
        return None
    return f"{parsed.scheme.casefold()}://{host}:{port}"


__all__ = [
    "ActionRiskPolicy",
    "ApprovalMode",
    "BrowserRiskContext",
    "RiskAssessment",
    "RiskDecision",
    "assess_tool_call",
]
