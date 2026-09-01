"""Tool executor that dispatches ToolCall objects to the registry."""

from __future__ import annotations

import asyncio
from collections.abc import Collection

from webagent.agent.context import planner_result_preview
from webagent.core.models import ToolCall, ToolResult
from webagent.tools.policy import PolicyDecision, ToolExecutionPolicy
from webagent.tools.registry import ToolRegistry, ToolSpec
from webagent.tools.risk import ActionRiskPolicy, RiskDecision

# Default per-tool wall-clock timeout.  Long-running tools (MinerU, PDF parse)
# should enforce their own internal timeouts; this is a safety net.
_DEFAULT_TOOL_TIMEOUT = 600  # 10 minutes


class ToolExecutor:
    """Thin wrapper that dispatches ToolCalls to a ToolRegistry."""

    def __init__(
        self,
        registry: ToolRegistry,
        tool_timeout: int = _DEFAULT_TOOL_TIMEOUT,
        *,
        allowed_tools: Collection[str] | None = None,
        policy: ToolExecutionPolicy | None = None,
        risk_policy: ActionRiskPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._tool_timeout = tool_timeout
        configured_tools = (
            frozenset(name.casefold() for name in allowed_tools)
            if allowed_tools is not None
            else None
        )
        policy_tools = (
            frozenset(name.casefold() for name in policy.allowed_tools) if policy else None
        )
        self._allowed_tools: frozenset[str] | None
        if configured_tools is not None and policy_tools is not None:
            self._allowed_tools = configured_tools & policy_tools
        else:
            self._allowed_tools = configured_tools if configured_tools is not None else policy_tools
        self._policy = policy
        self._risk_policy = risk_policy

    def get_tool_descriptions(self) -> str:
        descriptions = self._registry.descriptions(self._allowed_tools)
        notices = []
        if self._policy is not None:
            notices.append(self._policy.prompt_notice)
        if self._risk_policy is not None:
            notices.append(self._risk_policy.prompt_notice)
        return "\n".join([*notices, descriptions])

    def get_tool_specs(self) -> list[ToolSpec]:
        """Return provider-native function schemas for exactly the exposed tools."""
        return self._registry.specs(self._allowed_tools)

    def terminal_evidence_hint(self) -> str:
        """Return policy-owned final-answer evidence without exposing policy internals."""
        if self._policy is None:
            return ""
        provider = getattr(self._policy, "terminal_evidence_hint", None)
        if not callable(provider):
            return ""
        value = provider()
        return value if isinstance(value, str) else ""

    def planner_evidence_hint(self) -> str:
        """Return an actionable policy recovery hint for the next planner call."""
        if self._policy is None:
            return ""
        provider = getattr(self._policy, "planner_evidence_hint", None)
        if not callable(provider):
            return ""
        value = provider()
        return value if isinstance(value, str) else ""

    def validate_tool_call(self, tool_call: ToolCall) -> str | None:
        """Validate planner output before it consumes an environment action step."""
        name = (tool_call.tool_name or "").casefold()
        registry_error = self._registry.validate_call(name, tool_call.parameters or {})
        if registry_error is not None:
            return registry_error
        # Authorization still fails closed during execution. This optional preflight
        # only repairs recoverable evidence formatting/search mistakes within the
        # planner retry budget, so a bad final ``done`` does not consume the last step.
        validator = getattr(self._policy, "validate_planner_call", None)
        if not callable(validator):
            return None
        value = validator(tool_call)
        return value if isinstance(value, str) else None

    def reset_policy(self, task: str) -> None:
        """Reset per-task policy evidence before a new agent run."""
        if self._policy is not None:
            self._policy.reset(task)

    def export_policy_state(self) -> dict[str, object] | None:
        """Export checkpoint-safe policy progress when a policy is active."""
        if self._policy is None:
            return None
        exporter = getattr(self._policy, "export_state", None)
        if not callable(exporter):
            return None
        state = exporter()
        return state if isinstance(state, dict) else None

    def import_policy_state(self, state: dict[str, object], *, task: str) -> None:
        """Restore policy progress; absence of an import contract fails closed."""
        if self._policy is None:
            raise ValueError("cannot restore policy state without an active policy")
        importer = getattr(self._policy, "import_state", None)
        if not callable(importer):
            raise ValueError("active policy does not support checkpoint restore")
        importer(state, task=task)

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        name = (tool_call.tool_name or "").lower()
        params = tool_call.parameters or {}
        if self._allowed_tools is not None and name not in self._allowed_tools:
            audit = (
                self._policy.denial_audit(name, "tool is not exposed by the active policy")
                if self._policy is not None
                else {}
            )
            return ToolResult(
                success=False,
                tool_name=name,
                error=f"Tool '{name}' is not allowed in this evaluation",
                audit=audit,
            )
        decision: PolicyDecision | None = None
        if self._policy is not None:
            try:
                decision = await self._policy.authorize(tool_call)
            except Exception as exc:
                return ToolResult(
                    success=False,
                    tool_name=name,
                    error=f"Execution policy failed closed: {type(exc).__name__}: {exc}",
                    audit={"policy": self._policy.name, "decision": "deny"},
                )
            if not decision.allowed:
                return ToolResult(
                    success=False,
                    tool_name=name,
                    error=f"Policy denied tool call: {decision.reason}",
                    audit=decision.as_audit(self._policy.name),
                )
        risk_decision: RiskDecision | None = None
        if self._risk_policy is not None:
            try:
                risk_decision = await self._risk_policy.authorize(tool_call)
            except Exception as exc:
                return ToolResult(
                    success=False,
                    tool_name=name,
                    error=f"Risk policy failed closed: {type(exc).__name__}: {exc}",
                    audit={"risk": {"decision": "deny", "reason": "approval failed"}},
                )
            if not risk_decision.allowed:
                audit = (
                    decision.as_audit(self._policy.name)
                    if decision is not None and self._policy is not None
                    else {}
                )
                audit["risk"] = risk_decision.as_audit()
                return ToolResult(
                    success=False,
                    tool_name=name,
                    error=f"Risk policy denied tool call: {risk_decision.reason}",
                    audit=audit,
                )
        try:
            result = await asyncio.wait_for(
                self._registry.execute(name, params),
                timeout=self._tool_timeout,
            )
        except TimeoutError:
            result = ToolResult(
                success=False,
                tool_name=name,
                error=f"Tool '{name}' exceeded {self._tool_timeout}s timeout and was cancelled",
            )
        if self._policy is not None and decision is not None:
            visible_result = planner_result_preview(name, result.data, success=result.success)
            try:
                audit = await self._policy.record_result(
                    tool_call,
                    result,
                    decision,
                    planner_visible_result=visible_result,
                )
            except Exception as exc:
                return ToolResult(
                    success=False,
                    tool_name=name,
                    error=f"Execution policy failed closed while recording evidence: "
                    f"{type(exc).__name__}: {exc}",
                    audit={"policy": self._policy.name, "decision": "deny"},
                )
            result = result.model_copy(update={"audit": audit})
        if risk_decision is not None:
            audit = dict(result.audit)
            audit["risk"] = risk_decision.as_audit()
            result = result.model_copy(update={"audit": audit})
        return result
