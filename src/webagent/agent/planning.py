"""Planner context, bounded retries, action validation and confidence accounting."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from webagent.agent.evidence_store import record_event
from webagent.agent.strategy import (
    StrategyObservation,
)
from webagent.core.models import (
    BrowserState,
    PlannerAttempt,
    ToolCall,
)
from webagent.core.protocols import Planner

if TYPE_CHECKING:
    from webagent.agent.loop import WebAgent
    from webagent.agent.loop_state import LoopState as _LoopState

logger = logging.getLogger("webagent")


class PlanningCoordinator:
    """Planning work against the agent's single authoritative session state."""

    def __init__(self, agent: WebAgent) -> None:
        self._agent = agent

    async def elicit_terminal_confidence(self, state: _LoopState, step_count: int) -> None:
        """Collect confidence for every terminal status before benchmark judging."""
        if not self._agent.config.elicit_terminal_confidence:
            return
        if "success_probability" in state.final_result:
            return
        estimator = getattr(self._agent._planner, "estimate_task_success", None)
        if not callable(estimator):
            self._agent._runtime_events.append(
                {
                    "type": "confidence_unavailable",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "reason": "planner does not implement terminal confidence elicitation",
                }
            )
            return
        try:
            async with asyncio.timeout(self._agent.config.confidence_timeout_seconds):
                probability = await estimator(
                    task=self._agent._current_task,
                    status=self._agent._task_status.value,
                    history_text=self._agent._history.format_for_llm(),
                )
            state.final_result = {
                **state.final_result,
                "success_probability": float(probability),
                "confidence_source": "terminal_self_report",
                "confidence_elicited_at_step": max(step_count, 1),
            }
            self._agent._runtime_events.append(
                {
                    "type": "confidence_elicited",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "success_probability": float(probability),
                }
            )
        except Exception as exc:
            self._agent._runtime_events.append(
                {
                    "type": "confidence_unavailable",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    def _planning_history(self, browser_state: BrowserState, state: _LoopState | None) -> str:
        """Build history with current controller state and observed recovery evidence."""
        history_text = self._agent._history.format_for_llm()
        if state is not None and state.planning_state is not None:
            history_text += "\n\nCONTROLLER PLAN STATE:\n" + state.planning_state.prompt_summary()
        if state is not None and state.strategy_manager is not None:
            history_text += "\n\nCONTROLLER STRATEGY HINT: " + state.strategy_manager.prompt_hint
        evidence_hint_provider = getattr(self._agent._tool_executor, "planner_evidence_hint", None)
        evidence_hint = evidence_hint_provider() if callable(evidence_hint_provider) else ""
        if evidence_hint:
            history_text += "\n\nCONTROLLER EVIDENCE RECOVERY: " + evidence_hint
        transient_hint = _transient_page_recovery_hint(browser_state)
        if transient_hint is not None:
            history_text += "\n\nOBSERVED TRANSIENT PAGE: " + transient_hint
        return history_text

    @staticmethod
    def _with_action_budget(
        history_text: str, remaining_actions: int, terminal_evidence_hint: str
    ) -> str:
        """Reserve terminal actions without discarding already observed evidence."""
        if remaining_actions == 1:
            history_text = (
                f"{history_text}\n\n"
                "ACTION BUDGET: This is the final allowed action. If the observed evidence "
                "answers the task, call 'done' now with the answer and exact source URL. "
                "Do not spend the final action re-reading evidence already visible."
            )
            if terminal_evidence_hint:
                history_text += "\n\n" + terminal_evidence_hint
        elif remaining_actions == 2:
            history_text = (
                f"{history_text}\n\n"
                "ACTION BUDGET: Two actions remain. Reserve the final action for 'done'; "
                "perform at most one essential evidence-gathering action first."
            )
            if terminal_evidence_hint:
                history_text += "\n\n" + terminal_evidence_hint
        return history_text

    def _loop_recovery_hint(self, state: _LoopState | None, step_number: int) -> str:
        """Observe repeated actions and return the resulting strategy hint."""
        history_text = ""
        # Check for loops before planning — inject nudge into history so LLM sees it
        if self._agent.loop_detector:
            is_looping, nudge = self._agent.loop_detector.is_looping()
            if is_looping:
                logger.warning("Loop detected: %s", nudge)
                history_text = f"{history_text}\n\n⚠️ LOOP DETECTED: {nudge}"
                if state is not None and state.strategy_manager is not None:
                    loop_type = str(
                        self._agent.loop_detector.export_state().get("loop_type") or "loop"
                    )
                    update = state.strategy_manager.observe(
                        StrategyObservation(
                            tool_name="",
                            success=False,
                            progress=False,
                            loop_type=loop_type,
                        ),
                        step_number=step_number,
                    )
                    self._agent._apply_strategy_update(state, update, step_number=step_number)
                    history_text += "\n" + update.prompt_hint
        return history_text

    async def _plan_validated_action(
        self,
        browser_state: BrowserState,
        history_text: str,
        tool_descriptions: str,
        *,
        require_done: bool,
    ) -> tuple[ToolCall | None, str | None]:
        """Request one action and reject terminal-budget or parameter violations."""
        error: str | None = None
        tool_call = await self._agent._planner.plan_action(
            task=self._agent._current_task,
            browser_state=browser_state,
            history_text=history_text,
            available_tools=tool_descriptions,
        )
        if tool_call is None:
            error = "planner returned no executable tool call"
        elif require_done and tool_call.tool_name.casefold() != "done":
            error = (
                "final action must be done because visited evidence is already available; "
                "answer from that evidence instead of gathering or re-reading"
            )
            tool_call = None
        else:
            validator = getattr(self._agent._tool_executor, "validate_tool_call", None)
            if callable(validator):
                validation_error = validator(tool_call)
                if validation_error is not None:
                    error = f"invalid tool call: {validation_error}"
                    tool_call = None
        return tool_call, error

    async def plan_action(
        self,
        browser_state: BrowserState,
        step_number: int = 0,
        state: _LoopState | None = None,
    ) -> ToolCall | None:
        state = state or self._agent._active_loop_state
        if state is not None and state.strategy_manager is not None:
            browser_state = browser_state.model_copy(
                update={
                    "requires_visual": state.strategy_manager.state.current == "visual-grounding"
                }
            )
        tool_descriptions = self._agent._tool_executor.get_tool_descriptions()
        history_text = self._planning_history(browser_state, state)
        remaining_actions = max(self._agent.config.max_steps - step_number + 1, 0)
        terminal_evidence_hint_provider = getattr(
            self._agent._tool_executor, "terminal_evidence_hint", None
        )
        terminal_evidence_hint = (
            terminal_evidence_hint_provider() if callable(terminal_evidence_hint_provider) else ""
        )
        history_text = self._with_action_budget(
            history_text, remaining_actions, terminal_evidence_hint
        )
        history_text += self._loop_recovery_hint(state, step_number)

        # Inject vision status so the LLM knows whether image analysis tools work
        if (
            hasattr(self._agent._planner, "vision_actually_works")
            and not self._agent._planner.vision_actually_works
        ):
            history_text = (
                f"{history_text}\n\n"
                "⚠️ VISION DISABLED: The vision API is not working. Do NOT use analyze_image "
                "or read_image tools. Instead, use pdf_extract_text, pdf_search, or "
                "pdf_get_figure_info to get information from documents."
            )

        for attempt_number in range(1, self._agent.config.planner_max_attempts + 1):
            attempt_start = time.time()
            error: str | None = None
            timed_out = False
            try:
                tool_call, error = await self._plan_validated_action(
                    browser_state,
                    history_text,
                    tool_descriptions,
                    require_done=remaining_actions == 1 and bool(terminal_evidence_hint),
                )
            except Exception as exc:
                tool_call = None
                timed_out = isinstance(exc, TimeoutError)
                error = f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"
                logger.error("Planner error: %s", error)

            self._record_planner_attempt(
                step_number,
                attempt_number,
                attempt_start,
                tool_call is not None,
                error,
                observation_path=browser_state.observation_path,
            )
            if tool_call is not None:
                self._record_loop_action(tool_call, browser_state)
                return tool_call
            if timed_out:
                break
            repair = _planner_repair_hint(self._agent._planner)
            if error:
                repair = f"{error}. {repair}"
            history_text += "\n\nPREVIOUS PLANNER ATTEMPT FAILED: " + repair
        if state is not None and state.strategy_manager is not None:
            update = state.strategy_manager.observe(
                StrategyObservation(
                    success=False,
                    progress=False,
                    planner_failure=True,
                    error="planner returned no executable action",
                ),
                step_number=step_number,
            )
            self._agent._apply_strategy_update(state, update, step_number=step_number)
        return None

    def _record_planner_attempt(
        self,
        step_number: int,
        attempt_number: int,
        started_at: float,
        success: bool,
        error: str | None,
        *,
        observation_path: str | None = None,
    ) -> None:
        metadata = getattr(self._agent._planner, "last_call_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        observation_input = getattr(self._agent._planner, "last_planning_input_metadata", {})
        if not isinstance(observation_input, dict):
            observation_input = {}
        transport_retries = metadata.get("transport_retries", 0)
        if (
            not isinstance(transport_retries, int)
            or isinstance(transport_retries, bool)
            or transport_retries < 0
        ):
            transport_retries = 0
        self._agent._planner_attempts.append(
            PlannerAttempt(
                step_number=step_number,
                attempt_number=attempt_number,
                timestamp=datetime.now(UTC).isoformat(),
                duration_seconds=time.time() - started_at,
                success=success,
                error=error,
                observation_path=observation_path,
                observation_input=observation_input,
                transport_retries=transport_retries,
                response_length=metadata.get("response_length"),
                finish_reason=metadata.get("finish_reason"),
                prompt_tokens=metadata.get("prompt_tokens"),
                completion_tokens=metadata.get("completion_tokens"),
                total_tokens=metadata.get("total_tokens"),
                requested_output_mode=metadata.get("requested_output_mode"),
                effective_output_mode=metadata.get("effective_output_mode"),
                structured_fallbacks=(
                    [str(item) for item in metadata.get("structured_fallbacks", [])]
                    if isinstance(metadata.get("structured_fallbacks"), list)
                    else []
                ),
            )
        )
        record_event(
            self._agent.run_layout.root,
            "planner_attempt",
            step_number,
            attempt=self._agent._planner_attempts[-1].model_dump(mode="json"),
        )

    def _record_loop_action(self, tool_call: ToolCall, browser_state: BrowserState) -> None:
        if not self._agent.loop_detector:
            return
        content = browser_state.dom_summary
        if browser_state.observation_id:
            content = content.replace(browser_state.observation_id, "<observation>")
        page_hash = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()
        self._agent.loop_detector.add_action(
            tool_name=tool_call.tool_name,
            page_url=browser_state.url,
            page_hash=page_hash,
            parameters=tool_call.parameters,
            observation_id=browser_state.observation_id,
        )


def _transient_page_recovery_hint(browser_state: BrowserState) -> str | None:
    """Prioritize grounded in-place recovery when the page declares a transient error."""
    title = browser_state.title.casefold()
    body = browser_state.dom_summary.casefold()
    title_markers = (
        "service unavailable",
        "temporarily unavailable",
        "too many requests",
        "transient interruption",
    )
    body_markers = (
        "temporarily unavailable",
        "transient interruption",
        "retry this request",
        "retry this stage",
        "try again later",
    )
    if not any(marker in title for marker in title_markers) and not any(
        marker in body for marker in body_markers
    ):
        return None
    return (
        "The current page itself reports a temporary interruption. Stay grounded on this page: "
        "prefer its visible retry/reload control; otherwise use refresh or a bounded wait. Do not "
        "navigate to a blank or guessed URL unless newly observed evidence requires it."
    )


def _planner_repair_hint(planner: Planner) -> str:
    mode = getattr(planner, "effective_output_mode", None)
    if mode == "native-tools":
        return (
            "Return exactly one provider-native function tool call using a listed tool; "
            "do not put the action in assistant prose or content JSON."
        )
    return (
        "Return exactly one valid JSON action using a listed tool; "
        "do not include prose outside the JSON."
    )
