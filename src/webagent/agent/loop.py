"""Main agent loop: observe → think → act → record."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from PIL import Image

from webagent.agent.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    AgentCheckpoint,
    ArtifactRecord,
    BrowserResumeState,
    CheckpointStore,
    PendingAction,
    ReplayPolicy,
    checkpoint_fingerprint,
)
from webagent.agent.context import planner_context, planner_result_preview
from webagent.agent.history import SessionHistory
from webagent.agent.loop_detector import LoopDetector
from webagent.agent.state import PlanningState, validate_durable_note
from webagent.agent.strategy import (
    StrategyManager,
    StrategyObservation,
    StrategyState,
    StrategyUpdate,
)
from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot, wait_for_page_stability
from webagent.core.config import AgentConfig
from webagent.core.models import (
    AgentResult,
    AgentStep,
    BrowserState,
    PlannerAttempt,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from webagent.core.protocols import AgentHook, Planner
from webagent.evaluation.artifacts import RunLayout
from webagent.evaluation.trace_schema import build_run_trace_v8
from webagent.tools.executor import ToolExecutor
from webagent.tools.risk import assess_tool_call
from webagent.utils.images import is_blank_image
from webagent.utils.runtime import package_source_fingerprint

logger = logging.getLogger("webagent")

# Exception messages that indicate the browser process went away.
_BROWSER_DISCONNECT_MESSAGES = (
    "target closed",
    "browser has been closed",
    "connection closed",
    "target page",
)


class TracePersistenceError(RuntimeError):
    """Raised when a strict run cannot persist its auditable trace and certificate."""


class _LoopState:
    """Mutable per-turn bookkeeping shared across one task's logical steps."""

    def __init__(
        self,
        start_time: float,
        *,
        run_id: str | None = None,
        resume_count: int = 0,
        resumed: bool = False,
        next_step: int = 1,
        planning_state: PlanningState | None = None,
        strategy_manager: StrategyManager | None = None,
    ) -> None:
        self.start_time = start_time
        self.run_id = run_id or str(uuid4())
        self.resume_count = resume_count
        self.resumed = resumed
        self.next_step = next_step
        self.consecutive_failures = 0
        self.last_figure_path: str | None = None
        self.final_result: dict[str, Any] = {}
        self.planning_state = planning_state
        self.strategy_manager = strategy_manager
        self.pending_action: PendingAction | None = None
        self.browser_state = BrowserResumeState()
        self.previous_checkpoint_sha256: str | None = None


def _next_history_step(steps: list[AgentStep]) -> int:
    """Return a monotonic session step even after a zero-step turn."""
    return max((step.step_number for step in steps), default=0) + 1


def _next_turn_index(layout: RunLayout) -> int:
    """Find the next non-overwriting turn index for resumed/legacy sessions."""
    indices: set[int] = set()
    if layout.trajectory_turns_dir.is_dir():
        for path in layout.trajectory_turns_dir.glob("turn-*.json"):
            if match := re.fullmatch(r"turn-(\d+)\.json", path.name):
                indices.add(int(match.group(1)))
    if layout.result_turns_dir.is_dir():
        for path in layout.result_turns_dir.glob("turn-*"):
            if path.is_dir() and (match := re.fullmatch(r"turn-(\d+)", path.name)):
                indices.add(int(match.group(1)))
    return max(indices, default=0) + 1


def _is_browser_disconnect(exc: Exception) -> bool:
    err_msg = str(exc).lower()
    return any(k in err_msg for k in _BROWSER_DISCONNECT_MESSAGES)


class WebAgent:
    """Autonomous web agent that executes natural-language tasks.

    The agent operates in a loop:
      1. **Observe** - capture screenshot + DOM state
      2. **Think** - ask the planner for the next action
      3. **Act** - execute the chosen tool
      4. **Record** - log results and update history
    """

    def __init__(
        self,
        planner: Planner,
        browser: BrowserController,
        tool_executor: ToolExecutor,
        config: AgentConfig | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self._planner = planner
        self._browser = browser
        self._tool_executor = tool_executor
        configure_tools = getattr(self._planner, "configure_tools", None)
        get_tool_specs = getattr(self._tool_executor, "get_tool_specs", None)
        if callable(configure_tools) and callable(get_tool_specs):
            configure_tools(get_tool_specs())
        self.config = config or AgentConfig()
        # Use explicit output_dir if provided, otherwise fall back to config
        self.output_dir = (
            Path(output_dir).expanduser().resolve() if output_dir else self.config.output_dir
        )
        self.run_layout = RunLayout.from_root(self.output_dir)

        self._history = SessionHistory(
            context_length=self.config.history_context_length,
            full_result_steps=self.config.history_full_result_steps,
        )
        self._hooks: list[AgentHook] = []
        self._current_task = ""
        self._task_status = TaskStatus.PENDING
        self._planner_attempts: list[PlannerAttempt] = []
        self._runtime_events: list[dict[str, Any]] = []
        self._checkpoint_store: CheckpointStore | None = None
        self._active_loop_state: _LoopState | None = None
        self._session_run_id: str | None = None
        self._session_turn_index = 0

        # Loop detection
        self.loop_detector: LoopDetector | None = None
        if self.config.enable_loop_detection:
            effective_window = min(
                self.config.loop_window_size,
                max(3, self.config.max_steps // 2),
            )
            self.loop_detector = LoopDetector(
                window_size=effective_window,
                threshold=min(self.config.loop_threshold, effective_window),
            )

        # Captcha handling state
        self._captcha_pause = self.config.captcha_pause

    # -- Lifecycle --------------------------------------------------------

    def add_hook(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def _prepare_run_output_dir(
        self, *, run_id: str | None = None, task: str | None = None
    ) -> None:
        """Prepare only an ownership-marked exact run directory."""
        self.run_layout = RunLayout.from_root(self.output_dir)
        self.run_layout.prepare(
            run_id=run_id or str(uuid4()),
            task=task or self._current_task or "unspecified task",
            model=self.config.model_name,
        )
        self.output_dir = self.run_layout.root

    async def run(
        self,
        task: str,
        max_steps: int | None = None,
        reset_history: bool = True,
        resume_from: str | Path | None = None,
    ) -> AgentResult:
        """Run a task, a same-session follow-up, or a checkpoint continuation.

        ``reset_history=False`` denotes a new turn in the current normal-mode
        session. It keeps the owned run and artifacts, while step numbers and
        immutable turn snapshots continue monotonically.
        """
        if resume_from is not None and (
            self.config.strict_eval_mode or self.config.search_engine_only
        ):
            raise ValueError("strict-eval runs cannot resume from checkpoints")
        follow_up = bool(
            resume_from is None and not reset_history and self._session_run_id is not None
        )
        if follow_up and (self.config.strict_eval_mode or self.config.search_engine_only):
            raise ValueError("strict-eval runs cannot combine multiple session turns")
        if resume_from is not None:
            resume_path = Path(resume_from).expanduser().resolve()
            self.output_dir = RunLayout.root_from_checkpoint(resume_path)
            self.run_layout = RunLayout.from_root(self.output_dir)
        reset_policy = getattr(self._tool_executor, "reset_policy", None)
        if callable(reset_policy):
            reset_policy(task)
        if max_steps is None:
            max_steps = self.config.max_steps
        if reset_history and resume_from is None:
            self._history.clear()
            self._planner_attempts.clear()
            self._runtime_events.clear()
            self._session_run_id = None
            self._session_turn_index = 0
            # The detector persists recent_actions/url_history across runs; reset
            # it so a second task on the same instance can't fire a false loop nudge.
            if self.loop_detector is not None:
                self.loop_detector.reset()

        self._current_task = task
        self._task_status = TaskStatus.RUNNING

        if follow_up:
            assert self._session_run_id is not None
            self._session_turn_index += 1
            state = _LoopState(
                start_time=time.time(),
                run_id=self._session_run_id,
                next_step=_next_history_step(self._history.steps),
                planning_state=_initial_planning_state(task),
                strategy_manager=self._new_strategy_manager(),
            )
            self._checkpoint_store = (
                CheckpointStore(self.run_layout.checkpoints_dir / self.config.checkpoint_filename)
                if self.config.checkpoint_enabled
                else None
            )
            if self._checkpoint_store is not None and self._checkpoint_store.exists():
                state.previous_checkpoint_sha256 = self._checkpoint_store.digest()
        elif resume_from is None:
            state = _LoopState(
                start_time=time.time(),
                planning_state=_initial_planning_state(task),
                strategy_manager=self._new_strategy_manager(),
            )
            self._prepare_run_output_dir(run_id=state.run_id, task=task)
            self._session_run_id = state.run_id
            self._session_turn_index = 1
            self._checkpoint_store = (
                CheckpointStore(self.run_layout.checkpoints_dir / self.config.checkpoint_filename)
                if self.config.checkpoint_enabled
                and not (self.config.strict_eval_mode or self.config.search_engine_only)
                else None
            )
        else:
            self._checkpoint_store = CheckpointStore(resume_from)
            state = await self._restore_checkpoint(task)
            # Adopt legacy output only after its checkpoint passed integrity,
            # task, configuration, and source-revision validation.
            self.run_layout.ensure_for_resume(
                run_id=state.run_id,
                task=task,
                model=self.config.model_name,
            )
            self._session_run_id = state.run_id
            self._session_turn_index = _next_turn_index(self.run_layout)
            current_checkpoint = self.run_layout.checkpoints_dir / self.config.checkpoint_filename
            if self._checkpoint_store.path != current_checkpoint.resolve():
                self._checkpoint_store = CheckpointStore(current_checkpoint)

        turn_start_step = state.next_step if follow_up else 1
        turn_planner_attempt_start = len(self._planner_attempts) if follow_up else 0
        turn_event_start = len(self._runtime_events) if follow_up else 0

        for hook in self._hooks:
            await hook.on_task_start(task)

        step_count = state.next_step - 1
        try:
            if state.pending_action is not None and state.pending_action.replay_policy != "safe":
                self._task_status = TaskStatus.BLOCKED
                self._runtime_events.append(
                    {
                        "type": "resume_pending_action_blocked",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "tool": state.pending_action.tool_name,
                        "external_effect": state.pending_action.external_effect,
                        "replay_policy": state.pending_action.replay_policy,
                    }
                )
            else:
                if state.pending_action is not None:
                    self._runtime_events.append(
                        {
                            "type": "resume_pending_action_discarded",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "tool": state.pending_action.tool_name,
                            "reason": "safe action outcome was ambiguous; planner must re-observe",
                        }
                    )
                    state.pending_action = None
                step_limit = state.next_step + max_steps - 1 if follow_up else max_steps
                step_count = await self._run_steps(state, step_limit)
        finally:
            await self._elicit_terminal_confidence(state, step_count)
            total_duration = time.time() - state.start_time
            for hook in self._hooks:
                await hook.on_task_end(self._task_status.value, step_count)

            result = AgentResult(
                success=self._task_status == TaskStatus.COMPLETED,
                status=self._task_status.value,
                steps_taken=step_count,
                total_duration=total_duration,
                final_result=state.final_result,
                history=self._history.steps,
                planner_attempts=list(self._planner_attempts),
                events=list(self._runtime_events),
            )
            await self._save_checkpoint(state, status=self._task_status.value)
            _ensure_turn_result_snapshot(
                self.run_layout,
                turn_index=self._session_turn_index,
                final_result=state.final_result,
                last_figure_path=state.last_figure_path,
            )
            _persist_run_trace(
                self.output_dir,
                task,
                result,
                self.config,
                run_id=state.run_id,
                resume_count=state.resume_count,
                resumed=state.resumed,
                turn_index=self._session_turn_index,
                turn_start_step=turn_start_step,
                planner_attempt_start=turn_planner_attempt_start,
                event_start=turn_event_start,
            )
        return result

    async def _elicit_terminal_confidence(self, state: _LoopState, step_count: int) -> None:
        """Collect confidence for every terminal status before benchmark judging."""
        if not self.config.elicit_terminal_confidence:
            return
        if "success_probability" in state.final_result:
            return
        estimator = getattr(self._planner, "estimate_task_success", None)
        if not callable(estimator):
            self._runtime_events.append(
                {
                    "type": "confidence_unavailable",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "reason": "planner does not implement terminal confidence elicitation",
                }
            )
            return
        try:
            async with asyncio.timeout(self.config.confidence_timeout_seconds):
                probability = await estimator(
                    task=self._current_task,
                    status=self._task_status.value,
                    history_text=self._history.format_for_llm(),
                )
            state.final_result = {
                **state.final_result,
                "success_probability": float(probability),
                "confidence_source": "terminal_self_report",
                "confidence_elicited_at_step": max(step_count, 1),
            }
            self._runtime_events.append(
                {
                    "type": "confidence_elicited",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "success_probability": float(probability),
                }
            )
        except Exception as exc:
            self._runtime_events.append(
                {
                    "type": "confidence_unavailable",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    async def _run_steps(self, state: _LoopState, max_steps: int) -> int:
        """Execute bounded logical steps and normalize terminal exceptions."""
        step_count = state.next_step - 1
        try:
            for step_count in range(state.next_step, max_steps + 1):
                remaining = self.config.task_timeout - (time.time() - state.start_time)
                if remaining <= 0:
                    self._task_status = TaskStatus.TIMEOUT
                    break
                try:
                    async with asyncio.timeout(remaining):
                        should_continue = await self._execute_step(step_count, state)
                except TimeoutError:
                    self._task_status = TaskStatus.TIMEOUT
                    self._runtime_events.append(
                        {
                            "type": "task_deadline_exceeded",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "step_number": step_count,
                            "task_timeout_seconds": self.config.task_timeout,
                        }
                    )
                    break
                if not should_continue:
                    break
            else:
                self._task_status = TaskStatus.MAX_STEPS_REACHED
        except (KeyboardInterrupt, asyncio.CancelledError):
            self._task_status = TaskStatus.INTERRUPTED
        except Exception as exc:
            self._handle_step_exception(exc)
        return step_count

    def _new_strategy_manager(self) -> StrategyManager | None:
        if not self.config.strategy_enabled:
            return None
        return StrategyManager(
            failure_threshold=self.config.strategy_failure_threshold,
            no_progress_threshold=self.config.strategy_no_progress_threshold,
            max_switches=self.config.strategy_max_switches,
        )

    async def _restore_checkpoint(self, task: str) -> _LoopState:
        assert self._checkpoint_store is not None
        checkpoint = self._checkpoint_store.load(
            expected_task=task,
            expected_config_fingerprint=_config_fingerprint(self.config),
            expected_source_fingerprint=package_source_fingerprint(),
        )
        if checkpoint.status in {"completed", "blocked"}:
            raise ValueError(f"checkpoint status {checkpoint.status!r} is terminal")
        missing = self._checkpoint_store.missing_artifacts(checkpoint, root=self.output_dir)
        if missing:
            raise ValueError("checkpoint artifacts are missing or changed: " + ", ".join(missing))
        self._history.restore_serialized(checkpoint.history)
        self._planner_attempts = [
            PlannerAttempt.model_validate(item) for item in checkpoint.planner_attempts
        ]
        self._runtime_events = [dict(item) for item in checkpoint.events]
        if self.loop_detector is not None and checkpoint.loop_state:
            self.loop_detector.import_state(checkpoint.loop_state)
        if checkpoint.policy_state:
            importer = getattr(self._tool_executor, "import_policy_state", None)
            if not callable(importer):
                raise ValueError("active execution policy cannot restore checkpoint state")
            importer(checkpoint.policy_state, task=task)
        strategy_manager = self._new_strategy_manager()
        if strategy_manager is not None:
            strategy_manager.restore(checkpoint.strategy_state)
        await self._browser.restore_checkpoint_state(
            {
                "schema_version": 1,
                "tabs": list(checkpoint.browser_state.tab_urls) or ["about:blank"],
                "active_index": checkpoint.browser_state.active_tab_index,
            }
        )
        state = _LoopState(
            start_time=time.time() - checkpoint.elapsed_seconds,
            run_id=checkpoint.run_id,
            resume_count=checkpoint.resume_count + 1,
            resumed=True,
            next_step=checkpoint.next_step,
            planning_state=checkpoint.planning_state or _initial_planning_state(task),
            strategy_manager=strategy_manager,
        )
        state.consecutive_failures = checkpoint.consecutive_failures
        state.last_figure_path = checkpoint.last_figure_path
        state.final_result = dict(checkpoint.final_result)
        state.pending_action = checkpoint.pending_action
        state.browser_state = checkpoint.browser_state
        state.previous_checkpoint_sha256 = self._checkpoint_store.digest()
        self._runtime_events.append(
            {
                "type": "run_resumed",
                "timestamp": datetime.now(UTC).isoformat(),
                "run_id": checkpoint.run_id,
                "resume_count": state.resume_count,
                "next_step": state.next_step,
            }
        )
        return state

    async def _save_checkpoint(self, state: _LoopState, *, status: str) -> None:
        if self._checkpoint_store is None:
            return
        try:
            exported = await self._browser.export_checkpoint_state(include_storage=False)
            tab_urls = tuple(_checkpoint_tab_url(url) for url in exported.get("tabs", []))
            state.browser_state = BrowserResumeState(
                current_url=tab_urls[exported["active_index"]] if tab_urls else None,
                tab_urls=tab_urls,
                active_tab_index=int(exported.get("active_index", 0)),
            )
        except Exception as exc:
            logger.warning("Could not capture browser checkpoint coordinates: %s", exc)
        policy_state: dict[str, Any] = {}
        exporter = getattr(self._tool_executor, "export_policy_state", None)
        if callable(exporter):
            exported_policy = exporter()
            if isinstance(exported_policy, dict):
                policy_state = exported_policy
        checkpoint = AgentCheckpoint(
            run_id=state.run_id,
            resume_count=state.resume_count,
            task_sha256=hashlib.sha256(self._current_task.encode("utf-8")).hexdigest(),
            status=status,
            next_step=state.next_step,
            elapsed_seconds=max(0.0, time.time() - state.start_time),
            config_fingerprint=_config_fingerprint(self.config),
            source_fingerprint=package_source_fingerprint(),
            history=tuple(_checkpoint_step(step, self.output_dir) for step in self._history.steps),
            planner_attempts=tuple(
                _checkpoint_planner_attempt(attempt) for attempt in self._planner_attempts
            ),
            events=tuple(
                _checkpoint_value(event, self.output_dir) for event in self._runtime_events
            ),
            planning_state=_checkpoint_planning_state(state.planning_state, self.output_dir),
            strategy_state=(
                _checkpoint_strategy_state(state.strategy_manager.state)
                if state.strategy_manager is not None
                else None
            )
            or StrategyManager().state,
            loop_state=(
                _checkpoint_loop_state(self.loop_detector.export_state())
                if self.loop_detector
                else {}
            ),
            policy_state=_checkpoint_value(policy_state, self.output_dir),
            browser_state=state.browser_state,
            artifacts=_checkpoint_artifacts(self._history.steps, self.output_dir),
            last_figure_path=(
                _checkpoint_local_path(state.last_figure_path, self.output_dir)
                if state.last_figure_path
                else None
            ),
            consecutive_failures=state.consecutive_failures,
            pending_action=state.pending_action,
            final_result={},
            previous_checkpoint_sha256=state.previous_checkpoint_sha256,
        )
        self._checkpoint_store.save(checkpoint)
        state.previous_checkpoint_sha256 = self._checkpoint_store.digest()

    # -- Internal ---------------------------------------------------------

    def _handle_step_exception(self, exc: Exception) -> None:
        """Mark the run failed; swallow browser disconnects, re-raise the rest."""
        # Treat browser disconnection as a failure rather than a hard crash
        if _is_browser_disconnect(exc):
            logger.error("Browser disconnected unexpectedly: %s", exc)
            self._task_status = TaskStatus.FAILED
        else:
            self._task_status = TaskStatus.FAILED
            raise

    async def _execute_step(self, step_count: int, state: _LoopState) -> bool:
        """Run one observe → think → act → record step.

        Returns False when the task loop should stop (status already updated).
        """
        step_start = time.time()
        state.next_step = step_count

        if self._timed_out(state):
            self._task_status = TaskStatus.TIMEOUT
            return False

        browser_state = await self._observe()

        # Check for captcha before planning
        if self._captcha_pause:
            challenge_state = await self._handle_captcha()
            if challenge_state == "blocked":
                self._task_status = TaskStatus.BLOCKED
                await self._save_checkpoint(state, status=self._task_status.value)
                return False
            if challenge_state == "resolved":
                browser_state = await self._observe()

        self._active_loop_state = state
        tool_call = await self._think(browser_state, step_count)
        if tool_call is None:
            _save_step_screenshot(browser_state, self._step_screenshot_path(step_count))
            state.consecutive_failures += 1
            state.next_step = step_count + 1
            await self._save_checkpoint(state, status="running")
            if state.consecutive_failures >= self.config.max_consecutive_failures:
                self._task_status = TaskStatus.FAILED
                return False
            return True

        assessment = assess_tool_call(tool_call)
        state.pending_action = PendingAction(
            tool_name=tool_call.tool_name,
            parameters_sha256=checkpoint_fingerprint(tool_call.parameters),
            external_effect=assessment.external_effect,
            replay_policy=_replay_policy(tool_call, assessment.approval_required),
        )
        # Write-ahead checkpoint: after this point a crash must never cause an
        # externally consequential action to be replayed blindly.
        await self._save_checkpoint(state, status="running")

        tool_start = time.time()
        tool_result = await self._act(tool_call)
        tool_duration = time.time() - tool_start

        # ``done`` is side-effect free for the browser. Reusing the pre-action
        # state avoids a redundant DOM/screenshot round trip on every success.
        if tool_call.tool_name == "done":
            post_action_state = browser_state
        else:
            # This delay belongs before the post-action observation. Previously
            # it ran after the screenshot was already persisted, so it could not
            # prevent captures of half-loaded pages.
            if self.config.post_action_wait_ms > 0:
                await asyncio.sleep(self.config.post_action_wait_ms / 1000)
            post_action_state = await self._observe()
        _save_step_screenshot(post_action_state, self._step_screenshot_path(step_count))

        self._update_failure_tracking(tool_call, tool_result, state)
        await self._record_step(
            step_count,
            step_start,
            browser_state,
            tool_call,
            tool_result,
            tool_duration,
        )
        state.pending_action = None
        state.next_step = step_count + 1
        self._observe_strategy_result(
            state,
            step_count=step_count,
            tool_call=tool_call,
            tool_result=tool_result,
            before=browser_state,
            after=post_action_state,
        )
        await self._save_checkpoint(state, status="running")

        # Check timeout after the outcome is durably recorded.
        if self._timed_out(state):
            self._task_status = TaskStatus.TIMEOUT
            return False

        if tool_call.tool_name == "done" and tool_result.success:
            self._task_status = TaskStatus.COMPLETED
            state.final_result = tool_result.data
            figure = _select_figure(
                state.final_result.get("attachments"),
                state.last_figure_path,
                self.run_layout.artifacts_dir,
            )
            canonical_figure = _persist_final_outputs(
                self.output_dir,
                state.final_result.get("summary", ""),
                figure,
                turn_index=self._session_turn_index,
            )
            _attach_figure(
                state.final_result,
                canonical_figure or figure,
                source_figure=figure if canonical_figure is not None else None,
                artifacts_dir=self.run_layout.artifacts_dir,
            )
            return False

        if state.consecutive_failures >= self.config.max_consecutive_failures:
            self._task_status = TaskStatus.FAILED
            return False

        return True

    def _timed_out(self, state: _LoopState) -> bool:
        return time.time() - state.start_time > self.config.task_timeout

    def _step_screenshot_path(self, step_count: int) -> Path:
        return self.run_layout.screenshots_dir / f"step_{step_count:03d}.jpg"

    async def _warn_if_captcha(self) -> None:
        """Compatibility wrapper using the bounded default human handoff."""
        await self._handle_captcha(handling="report")

    async def _handle_captcha(self, *, handling: str | None = None) -> str:
        """Report, fail, or wait for a human without attempting CAPTCHA bypass."""
        captcha_info = await self._check_for_captcha()
        if not captcha_info.get("detected"):
            return "clear"
        mode = handling or self.config.captcha_handling
        event = {
            "type": "captcha_detected",
            "timestamp": datetime.now(UTC).isoformat(),
            "url": self._browser.page.url,
            "challenge_type": captcha_info.get("type"),
            "confidence": captcha_info.get("confidence"),
            "reason": captcha_info.get("reason"),
            "handling": mode,
        }
        self._runtime_events.append(event)
        logger.warning(
            "Captcha detected: %s (confidence: %.1f) - %s [handling=%s]",
            captcha_info.get("type"),
            float(captcha_info.get("confidence") or 0.0),
            captcha_info.get("reason"),
            mode,
        )
        if mode == "fail" or self.config.browser_headless:
            if self.config.search_engine_only:
                event["outcome"] = "blocked"
                event["browser_closed"] = False
                event["browser_retained_for_isolated_reset"] = True
                return "blocked"
            return await self._block_captcha_and_close(event, outcome="blocked")

        deadline = time.monotonic() + self.config.captcha_wait_timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(self.config.captcha_poll_interval_seconds)
            current = await self._check_for_captcha()
            if not current.get("detected"):
                event["outcome"] = "resolved_by_human"
                event["resolved_at"] = datetime.now(UTC).isoformat()
                logger.info("Captcha challenge cleared by human intervention")
                return "resolved"
        return await self._block_captcha_and_close(event, outcome="human_wait_timeout")

    async def _block_captcha_and_close(self, event: dict[str, Any], *, outcome: str) -> str:
        """Fail closed and close the browser after an unresolved challenge."""
        event["outcome"] = outcome
        try:
            await self._browser.close()
            event["browser_closed"] = True
        except Exception as exc:
            event["browser_closed"] = False
            event["close_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Failed to close browser after unresolved CAPTCHA: %s", exc)
        return "blocked"

    def _update_failure_tracking(
        self, tool_call: ToolCall, tool_result: ToolResult, state: _LoopState
    ) -> None:
        """Reset or grow the consecutive-failure counter and track focused figures."""
        if not tool_result.success:
            state.consecutive_failures += 1
            return

        state.consecutive_failures = 0
        # Remember the most recent image the agent worked with so it
        # can be persisted as the "found figure" on completion.
        if tool_call.tool_name in _FIGURE_TOOLS:
            candidate = tool_result.data.get("path") or tool_result.data.get("image_path")
            if isinstance(candidate, str):
                state.last_figure_path = candidate

    def _observe_strategy_result(
        self,
        state: _LoopState,
        *,
        step_count: int,
        tool_call: ToolCall,
        tool_result: ToolResult,
        before: BrowserState,
        after: BrowserState,
    ) -> None:
        progress = _made_progress(tool_result, before, after)
        if tool_result.success and tool_result.data and state.planning_state is not None:
            preview = planner_result_preview(tool_call.tool_name, tool_result.data, success=True)
            source = tool_result.data.get("url") or tool_result.data.get("source_url")
            durable_note = tool_call.tool_name == "remember"
            summary = (
                str(tool_result.data.get("note", ""))
                if durable_note
                else f"{tool_call.tool_name}: {preview[:1800]}"
            )
            state.planning_state = state.planning_state.record_evidence(
                step_number=step_count,
                summary=summary,
                source=str(source)[:4000] if source else after.url or None,
                kind="durable_note" if durable_note else "tool_result",
            )
        if state.strategy_manager is None:
            return
        update = state.strategy_manager.observe(
            StrategyObservation(
                tool_name=tool_call.tool_name,
                success=tool_result.success,
                progress=progress,
                error=tool_result.error,
                policy_denied=_policy_was_denied(tool_result.audit),
            ),
            step_number=step_count,
        )
        self._apply_strategy_update(state, update, step_number=step_count)

    def _apply_strategy_update(
        self,
        state: _LoopState,
        update: StrategyUpdate,
        *,
        step_number: int,
    ) -> None:
        if update.switch is None and not update.replan_required:
            return
        if update.switch is not None:
            self._runtime_events.append(
                {
                    "type": "strategy_switch",
                    "timestamp": datetime.now(UTC).isoformat(),
                    **update.switch.model_dump(mode="json"),
                }
            )
        reason = (
            update.switch.reason
            if update.switch is not None
            else "available strategy routes exhausted"
        )
        self._runtime_events.append(
            {
                "type": "replan",
                "timestamp": datetime.now(UTC).isoformat(),
                "step_number": step_number,
                "reason": reason,
                "strategy": update.state.current,
                "exhausted": update.exhausted,
            }
        )
        if state.planning_state is not None:
            state.planning_state = state.planning_state.revise(
                step_number=step_number,
                reason=reason,
                strategy=update.state.current,
                milestone_descriptions=[update.prompt_hint],
            )

    async def _record_step(
        self,
        step_count: int,
        step_start: float,
        browser_state: BrowserState,
        tool_call: ToolCall,
        tool_result: ToolResult,
        tool_duration: float,
    ) -> None:
        agent_step = AgentStep(
            step_number=step_count,
            timestamp=datetime.now(UTC).isoformat(),
            browser_state=browser_state,
            tool_call=tool_call,
            tool_result=tool_result,
            duration_seconds=time.time() - step_start,
            tool_duration_seconds=tool_duration,
        )
        self._history.add(agent_step)

        for hook in self._hooks:
            await hook.on_step_complete(step_count, tool_call, tool_result)

    async def _observe(self) -> BrowserState:
        for attempt in range(3):
            try:
                try:
                    await self._browser.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass

                await wait_for_page_stability(
                    self._browser.page,
                    timeout_ms=self.config.observation_stability_timeout_ms,
                    stable_ms=self.config.observation_stable_ms,
                )

                use_cdp = self.config.use_cdp
                max_elements = self.config.max_snapshot_elements

                snapshot = await take_snapshot(
                    self._browser.page,
                    task=self._current_task,
                    use_cdp=use_cdp,
                    max_elements=max_elements,
                    filter_ads=self.config.enable_ad_filtering,
                    wait_after_load=0,
                )
                dom_summary = snapshot.get("markdown", "")
                screenshot = None
                raw = snapshot.get("screenshot_bytes")
                if raw:
                    try:
                        screenshot = Image.open(BytesIO(raw))
                    except Exception:
                        pass
                return BrowserState(
                    screenshot=screenshot,
                    dom_summary=dom_summary,
                    url=snapshot["meta"].get("url", ""),
                    title=snapshot["meta"].get("title", ""),
                    timestamp=datetime.now(UTC).isoformat(),
                )
            except Exception as e:
                logger.warning("Observe attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)
        return BrowserState(
            dom_summary="(page loading)",
            url=self._browser.page.url,
            title="",
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def _think(
        self,
        browser_state: BrowserState,
        step_number: int = 0,
        state: _LoopState | None = None,
    ) -> ToolCall | None:
        state = state or self._active_loop_state
        tool_descriptions = self._tool_executor.get_tool_descriptions()
        history_text = self._history.format_for_llm()
        if state is not None and state.planning_state is not None:
            history_text += "\n\nCONTROLLER PLAN STATE:\n" + state.planning_state.prompt_summary()
        if state is not None and state.strategy_manager is not None:
            history_text += "\n\nCONTROLLER STRATEGY HINT: " + state.strategy_manager.prompt_hint
        transient_hint = _transient_page_recovery_hint(browser_state)
        if transient_hint is not None:
            history_text += "\n\nOBSERVED TRANSIENT PAGE: " + transient_hint

        remaining_actions = max(self.config.max_steps - step_number + 1, 0)
        terminal_evidence_hint_provider = getattr(
            self._tool_executor, "terminal_evidence_hint", None
        )
        terminal_evidence_hint = (
            terminal_evidence_hint_provider() if callable(terminal_evidence_hint_provider) else ""
        )
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

        # Check for loops before planning — inject nudge into history so LLM sees it
        if self.loop_detector:
            is_looping, nudge = self.loop_detector.is_looping()
            if is_looping:
                logger.warning("Loop detected: %s", nudge)
                history_text = f"{history_text}\n\n⚠️ LOOP DETECTED: {nudge}"
                if state is not None and state.strategy_manager is not None:
                    loop_type = str(self.loop_detector.export_state().get("loop_type") or "loop")
                    update = state.strategy_manager.observe(
                        StrategyObservation(
                            tool_name="",
                            success=False,
                            progress=False,
                            loop_type=loop_type,
                        ),
                        step_number=step_number,
                    )
                    self._apply_strategy_update(state, update, step_number=step_number)
                    history_text += "\n" + update.prompt_hint

        # Inject vision status so the LLM knows whether image analysis tools work
        if (
            hasattr(self._planner, "vision_actually_works")
            and not self._planner.vision_actually_works
        ):
            history_text = (
                f"{history_text}\n\n"
                "⚠️ VISION DISABLED: The vision API is not working. Do NOT use analyze_image "
                "or read_image tools. Instead, use pdf_extract_text, pdf_search, or "
                "pdf_get_figure_info to get information from documents."
            )

        for attempt_number in range(1, self.config.planner_max_attempts + 1):
            attempt_start = time.time()
            error: str | None = None
            timed_out = False
            try:
                tool_call = await self._planner.plan_action(
                    task=self._current_task,
                    browser_state=browser_state,
                    history_text=history_text,
                    available_tools=tool_descriptions,
                )
                if tool_call is None:
                    error = "planner returned no executable tool call"
                else:
                    validator = getattr(self._tool_executor, "validate_tool_call", None)
                    if callable(validator):
                        validation_error = validator(tool_call)
                        if validation_error is not None:
                            error = f"invalid tool call: {validation_error}"
                            tool_call = None
            except Exception as exc:
                tool_call = None
                timed_out = isinstance(exc, TimeoutError)
                error = f"{type(exc).__name__}: {str(exc).strip() or repr(exc)}"
                logger.error("Planner error: %s", error)

            self._record_planner_attempt(
                step_number, attempt_number, attempt_start, tool_call is not None, error
            )
            if tool_call is not None:
                self._record_loop_action(tool_call, browser_state)
                return tool_call
            if timed_out:
                break
            repair = _planner_repair_hint(self._planner)
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
            self._apply_strategy_update(state, update, step_number=step_number)
        return None

    def _record_planner_attempt(
        self,
        step_number: int,
        attempt_number: int,
        started_at: float,
        success: bool,
        error: str | None,
    ) -> None:
        metadata = getattr(self._planner, "last_call_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        transport_retries = metadata.get("transport_retries", 0)
        if (
            not isinstance(transport_retries, int)
            or isinstance(transport_retries, bool)
            or transport_retries < 0
        ):
            transport_retries = 0
        self._planner_attempts.append(
            PlannerAttempt(
                step_number=step_number,
                attempt_number=attempt_number,
                timestamp=datetime.now(UTC).isoformat(),
                duration_seconds=time.time() - started_at,
                success=success,
                error=error,
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

    def _record_loop_action(self, tool_call: ToolCall, browser_state: BrowserState) -> None:
        if not self.loop_detector:
            return
        page_hash = hashlib.md5(
            browser_state.dom_summary.encode("utf-8", errors="replace")
        ).hexdigest()
        self.loop_detector.add_action(
            tool_name=tool_call.tool_name,
            page_url=browser_state.url,
            page_hash=page_hash,
            parameters=tool_call.parameters,
        )

    async def _check_for_captcha(self) -> dict[str, Any]:
        """Check if current page has a captcha challenge.

        Returns:
            Dictionary with captcha detection results.
        """
        return await self._browser.check_captcha()

    async def _act(self, tool_call: ToolCall) -> ToolResult:
        return await self._tool_executor.execute(tool_call)


_is_blank_screenshot = is_blank_image


def _initial_planning_state(task: str) -> PlanningState:
    return PlanningState.create(
        task,
        [
            "Discover grounded candidate sources and required prerequisites",
            "Execute the requested browser or document workflow",
            "Verify the evidence and return an honest final result",
        ],
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


def _config_fingerprint(config: AgentConfig) -> str:
    """Fingerprint compatibility-affecting, non-secret runtime settings."""
    # Include all behavior-affecting settings (provider URLs, parser/cache and
    # policy choices included), while excluding credentials and run location.
    value = config.model_dump(
        mode="json",
        exclude={
            "model_api_key",
            "google_search_api_key",
            "vllm_api_key",
            "marker_api_key",
            "mineru_api_key",
            "paddleocr_api_key",
            "output_dir",
            "checkpoint_filename",
        },
    )
    return checkpoint_fingerprint(value)


def _checkpoint_step(step: AgentStep, output_dir: Path) -> dict[str, Any]:
    """Build the minimum non-sensitive history representation needed to resume."""
    value = step.model_dump(mode="python")
    browser = value.get("browser_state")
    if isinstance(browser, dict):
        page_status = _checkpoint_page_status(
            f"{browser.get('title', '')} {browser.get('dom_summary', '')}"
        )
        browser.pop("screenshot", None)
        browser["dom_summary"] = "(omitted from checkpoint; re-observe current page)"
        browser["url"] = _checkpoint_tab_url(browser.get("url"))
        browser["title"] = page_status
    tool_call = value.get("tool_call")
    if isinstance(tool_call, dict):
        name = str(tool_call.get("tool_name", ""))
        params = tool_call.get("parameters")
        tool_call["parameters"] = _checkpoint_parameters(name, params, output_dir)
        # Model-authored rationale is not controller state and may echo page input.
        tool_call["reasoning"] = ""
    tool_result = value.get("tool_result")
    if isinstance(tool_result, dict):
        tool_result["data"] = _checkpoint_value(tool_result.get("data", {}), output_dir)
        tool_result["audit"] = _checkpoint_value(tool_result.get("audit", {}), output_dir)
        if tool_result.get("error") is not None:
            tool_result["error"] = "tool action failed; details omitted"
    return value


def _checkpoint_page_status(value: str) -> str:
    """Retain only a coarse non-sensitive page-state class across resume."""
    normalized = value.casefold()
    if any(marker in normalized for marker in ("transient", "temporarily unavailable", "503")):
        return "transient_error"
    if any(marker in normalized for marker in ("captcha", "verify you are human")):
        return "captcha_challenge"
    if any(marker in normalized for marker in ("access denied", "forbidden", "403")):
        return "access_denied"
    if any(marker in normalized for marker in ("not found", "404")):
        return "not_found"
    return "normal"


_CHECKPOINT_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "base64",
        "content",
        "cookie",
        "cookies",
        "data_url",
        "id_token",
        "password",
        "secret",
        "session",
        "session_id",
        "storage_state",
        "token",
    }
)
_CHECKPOINT_PATH_KEYS = frozenset({"path", "image_path", "output_dir", "upload_path"})
_CHECKPOINT_URL_KEYS = frozenset(
    {"url", "source_url", "browser_url", "current_url", "html_url", "pdf_url", "source"}
)
_CHECKPOINT_STRATEGY_VALUES = frozenset(
    {
        "default",
        "search-discovery",
        "semantic-dom",
        "alternate-navigation",
        "visual-grounding",
        "document-local",
        "recovery",
    }
)
_CHECKPOINT_FIXED_VALUES = frozenset(
    {
        "allow",
        "deny",
        "blocked",
        "completed",
        "failed",
        "fail",
        "forbid",
        "human_wait_timeout",
        "interrupted",
        "max_steps_reached",
        "none_or_reversible",
        "prompt",
        "reconcile",
        "report",
        "resolved_by_human",
        "running",
        "safe",
        "success",
        "timeout",
        "wait_for_human",
    }
)


def _checkpoint_parameters(tool_name: str, value: Any, output_dir: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized = _checkpoint_value(value, output_dir)
    if not isinstance(sanitized, dict):
        return {}
    if tool_name.casefold() in {"type", "frame_interact", "shadow_dom"} and "text" in sanitized:
        sanitized["text"] = "[redacted]"
    return sanitized


def _checkpoint_value(value: Any, output_dir: Path, key: str = "") -> Any:
    normalized = key.casefold()
    if normalized in _CHECKPOINT_SECRET_KEYS:
        return "[redacted]"
    if normalized in _CHECKPOINT_PATH_KEYS and isinstance(value, str):
        return _checkpoint_local_path(value, output_dir)
    if normalized in _CHECKPOINT_URL_KEYS and isinstance(value, str):
        return (
            _checkpoint_tab_url(value) if value.startswith(("http://", "https://")) else "[omitted]"
        )
    if normalized == "attachments" and isinstance(value, list):
        return [
            _checkpoint_tab_url(item)
            if isinstance(item, str) and item.startswith(("http://", "https://"))
            else _checkpoint_local_path(item, output_dir)
            if isinstance(item, str)
            else "[redacted]"
            for item in value
        ]
    if isinstance(value, dict):
        return {
            _checkpoint_dict_key(str(item_key)): _checkpoint_value(item, output_dir, str(item_key))
            for item_key, item in value.items()
            if str(item_key).casefold() != "task"
        }
    if isinstance(value, (list, tuple)):
        return [_checkpoint_value(item, output_dir, key) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _checkpoint_tab_url(value)
        if _checkpoint_safe_string(normalized, value):
            return value
        return "[omitted]"
    return value


def _checkpoint_dict_key(value: str) -> str:
    """Keep schema-like keys while hashing keys that can themselves carry secrets."""
    normalized = value.casefold()
    if (
        any(secret in normalized for secret in _CHECKPOINT_SECRET_KEYS)
        or "/" in value
        or "\\" in value
        or "://" in value
    ):
        return f"field_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
    return value


def _checkpoint_safe_string(key: str, value: str) -> bool:
    """Allow only controller-generated scalar strings, never arbitrary page text."""
    if value in _CHECKPOINT_FIXED_VALUES or value in _CHECKPOINT_STRATEGY_VALUES:
        return True
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return True
    if key in {"timestamp", "date", "datetime"}:
        return re.fullmatch(r"[0-9TtZz:+.\- ]{4,40}", value) is not None
    if key in {
        "engine",
        "finish_reason",
        "loop_type",
        "policy",
        "requested_output_mode",
        "effective_output_mode",
        "source_tool",
        "tool",
        "tool_name",
        "type",
    }:
        return re.fullmatch(r"[A-Za-z0-9_.:\-]{1,100}", value) is not None
    if key in {"figure_number", "table_number"}:
        return re.fullmatch(r"[A-Za-z0-9_.:\-]{1,32}", value) is not None
    if key == "version_frontier":
        return re.fullmatch(r"[A-Za-z0-9_.+\-]{1,100}", value) is not None
    return False


def _checkpoint_local_path(value: str, output_dir: Path) -> str:
    raw = Path(value).expanduser()
    output_root = output_dir.resolve()
    possible = [raw] if raw.is_absolute() else [output_root / "artifacts" / raw, output_root / raw]
    path = next((item.resolve() for item in possible if item.exists()), None)
    if path is None or (path != output_root and not path.is_relative_to(output_root)):
        return "[redacted]"
    return path.relative_to(output_root).as_posix()


def _checkpoint_planner_attempt(attempt: PlannerAttempt) -> dict[str, Any]:
    value = attempt.model_dump(mode="json")
    if value.get("error") is not None:
        value["error"] = "planner attempt failed; details omitted"
    return value


def _checkpoint_planning_state(
    state: PlanningState | None, output_dir: Path
) -> PlanningState | None:
    if state is None:
        return None
    milestone_ids = {item.id: f"m{index}" for index, item in enumerate(state.milestones, 1)}
    return PlanningState.model_validate(
        {
            "objective": "task bound by checkpoint task_sha256",
            "milestones": [
                {
                    "id": milestone_ids[item.id],
                    "description": f"checkpoint milestone {index}",
                    "status": item.status,
                    "completed_at_step": item.completed_at_step,
                }
                for index, item in enumerate(state.milestones, 1)
            ],
            "active_milestone_id": (
                milestone_ids.get(state.active_milestone_id)
                if state.active_milestone_id is not None
                else None
            ),
            "evidence": [
                {
                    "id": f"e{index}",
                    "step_number": item.step_number,
                    "kind": item.kind if item.kind == "durable_note" else "checkpoint",
                    "summary": (
                        validate_durable_note(item.summary)
                        if item.kind == "durable_note"
                        else f"evidence retained from step {item.step_number}"
                    ),
                    "source": (
                        _checkpoint_tab_url(item.source)
                        if item.source and item.source.startswith(("http://", "https://"))
                        else _checkpoint_local_path(item.source, output_dir)
                        if item.source
                        else None
                    ),
                }
                for index, item in enumerate(state.evidence, 1)
            ],
            "revisions": [
                {
                    "revision": index,
                    "step_number": item.step_number,
                    "reason": "runtime strategy replan",
                    "strategy": (
                        item.strategy
                        if item.strategy in _CHECKPOINT_STRATEGY_VALUES
                        else "recovery"
                    ),
                    "added_milestone_ids": tuple(
                        milestone_ids[milestone_id]
                        for milestone_id in item.added_milestone_ids
                        if milestone_id in milestone_ids
                    ),
                }
                for index, item in enumerate(state.revisions, 1)
            ],
        }
    )


def _checkpoint_strategy_state(state: StrategyState) -> StrategyState:
    switches = tuple(
        item.model_copy(update={"reason": "runtime strategy switch"}) for item in state.switches
    )
    return state.model_copy(update={"switches": switches})


def _checkpoint_loop_state(state: dict[str, Any]) -> dict[str, Any]:
    safe = dict(state)
    for key in ("recent_actions", "recent_pages"):
        values = safe.get(key)
        safe[key] = (
            [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in values]
            if isinstance(values, list) and all(isinstance(item, str) for item in values)
            else []
        )
    urls = safe.get("url_history")
    safe["url_history"] = (
        [_checkpoint_tab_url(item) for item in urls]
        if isinstance(urls, list) and all(isinstance(item, str) for item in urls)
        else []
    )
    return safe


def _checkpoint_artifacts(steps: list[AgentStep], output_dir: Path) -> tuple[ArtifactRecord, ...]:
    """Record only explicit tool-returned files; never crawl the output tree."""
    output_root = output_dir.resolve()
    artifacts_root = output_root / "artifacts"
    candidates: list[str] = []
    for step in steps:
        data = step.tool_result.data
        for key in ("path", "image_path"):
            value = data.get(key)
            if isinstance(value, str):
                candidates.append(value)
        attachments = data.get("attachments")
        if isinstance(attachments, list):
            candidates.extend(item for item in attachments if isinstance(item, str))

    records: list[ArtifactRecord] = []
    seen: set[Path] = set()
    for value in candidates:
        raw = Path(value).expanduser()
        possible = [raw] if raw.is_absolute() else [artifacts_root / raw, output_root / raw]
        path = next((item.resolve() for item in possible if item.is_file()), None)
        if path is None or path in seen:
            continue
        if path != output_root and not path.is_relative_to(output_root):
            continue
        relative = path.relative_to(output_root)
        if (
            relative.parts[0] == "screenshots"
            or relative.parts[:2]
            == (
                "observations",
                "screenshots",
            )
            or relative.as_posix()
            in {
                "artifacts/checkpoint.json",
                "artifacts/run.json",
                "control/checkpoints/latest.json",
                "trajectory/trace.json",
            }
        ):
            continue
        records.append(ArtifactRecord.from_path(path, root=output_root))
        seen.add(path)
    return tuple(records)


def _checkpoint_tab_url(value: Any) -> str:
    """Keep web coordinates while dropping local paths and common URL credentials."""
    if not isinstance(value, str):
        return "about:blank"
    if value == "about:blank":
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "about:blank"
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{parsed.hostname}{port}"
    # Query strings are free-form and routinely carry search text, reset
    # tokens, OAuth state, and signed URLs under provider-specific key names.
    # A non-secret checkpoint therefore retains only origin + path.
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _made_progress(result: ToolResult, before: BrowserState, after: BrowserState) -> bool:
    if not result.success:
        return False
    if before.url != after.url or before.dom_summary != after.dom_summary:
        return True
    if result.tool_name.casefold() in {
        "click_link",
        "get_url",
        "search",
        "screenshot",
        "wait",
        "wait_for_element",
        "inspect_frames",
        "inspect_shadow_dom",
    }:
        return False
    return bool(result.data)


def _policy_was_denied(audit: dict[str, Any]) -> bool:
    if audit.get("decision") == "deny":
        return True
    risk = audit.get("risk")
    return isinstance(risk, dict) and risk.get("decision") == "deny"


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


def _replay_policy(tool_call: ToolCall, approval_required: bool) -> ReplayPolicy:
    """Classify crash replay conservatively without relying on missing DOM context."""
    if approval_required:
        return "forbid"
    if tool_call.tool_name.casefold() in {
        "click",
        "click_link",
        "press",
        "type",
        "select_dropdown",
        "frame_interact",
        "shadow_dom",
        "upload_file",
    }:
        return "reconcile"
    return "safe"


def _save_step_screenshot(
    browser_state: BrowserState,
    screenshot_path: Path,
) -> None:
    if browser_state.screenshot is None:
        return
    encoded = BytesIO()
    browser_state.screenshot.save(encoded, format="JPEG")
    payload = encoded.getvalue()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    match = re.fullmatch(r"step_(\d+)\.jpg", screenshot_path.name)
    previous = (
        screenshot_path.with_name(f"step_{int(match.group(1)) - 1:03d}.jpg")
        if match is not None and int(match.group(1)) > 1
        else None
    )
    if previous is not None and previous.is_file():
        try:
            if previous.read_bytes() == payload:
                os.link(previous, screenshot_path)
                return
        except OSError:
            pass
    screenshot_path.write_bytes(payload)


# Tools whose successful result identifies an image the agent focused on; the
# most recent one is the "found figure" persisted when the task completes.
# pdf_analyze_figure resolves a numbered figure and returns it under image_path.
_FIGURE_TOOLS = ("pdf_analyze_figure", "analyze_image", "read_image", "save_image")
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _as_image_path(candidate: Any, artifacts_dir: Path) -> Path | None:
    """Resolve ``candidate`` to an existing image file contained in the output root.

    Attachments are partly LLM-controlled, so the resolved path is confined to
    the output root to avoid copying an arbitrary file out of the workspace.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    path = Path(candidate.strip())
    if not path.is_absolute():
        path = artifacts_dir / path
    path = path.resolve()
    output_root = artifacts_dir.resolve().parent
    if path != output_root and not path.is_relative_to(output_root):
        return None
    if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
        return path
    return None


def _select_figure(
    attachments: Any, last_figure_path: str | None, artifacts_dir: Path
) -> Path | None:
    """Pick the figure to persist: first image attachment, else the last one seen."""
    if isinstance(attachments, list):
        for attachment in attachments:
            found = _as_image_path(attachment, artifacts_dir)
            if found:
                return found
    return _as_image_path(last_figure_path, artifacts_dir)


def _attach_figure(
    final_result: dict[str, Any],
    figure: Path | None,
    *,
    source_figure: Path | None = None,
    artifacts_dir: Path | None = None,
) -> None:
    """Ensure the found figure is listed in the result's ``attachments``.

    The ``done`` tool's attachments are model-controlled and are often omitted
    even when a figure was analyzed. Backfilling keeps the reported result
    complete without depending on the model remembering to attach the image.
    """
    if figure is None:
        return
    attachments = final_result.get("attachments")
    if not isinstance(attachments, list):
        attachments = []
    if source_figure is not None and artifacts_dir is not None:
        source = source_figure.resolve()
        attachments = [
            attachment
            for attachment in attachments
            if _as_image_path(attachment, artifacts_dir) != source
        ]
    figure_str = str(figure)
    if figure_str not in attachments:
        attachments.append(figure_str)
    final_result["attachments"] = attachments


def _persist_final_outputs(
    output_dir: Path,
    summary: str,
    figure: Path | None,
    *,
    turn_index: int | None = None,
) -> Path | None:
    """Write the final answer and copy the selected figure attachment.

    Best-effort: failures are logged, never raised, so persistence cannot crash a
    task that has otherwise completed successfully.
    """
    layout = RunLayout.from_root(output_dir)
    figure_bytes: bytes | None = None
    figure_name: str | None = None
    if figure is not None:
        try:
            figure_bytes = figure.read_bytes()
            figure_name = f"figure{figure.suffix.lower()}"
        except OSError as exc:
            logger.warning("Failed to read figure %s: %s", figure, exc)
    try:
        layout.result_dir.mkdir(parents=True, exist_ok=True)
        layout.summary_path.write_text(summary or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write result/summary.txt: %s", exc)

    dest: Path | None = None
    try:
        if layout.attachments_dir.exists():
            shutil.rmtree(layout.attachments_dir)
        layout.attachments_dir.mkdir(parents=True, exist_ok=True)
        if figure_bytes is not None and figure_name is not None:
            dest = layout.attachments_dir / figure_name
            _link_or_write_attachment(figure, figure_bytes, dest)
            logger.info("Saved found figure to %s", dest)
    except OSError as exc:
        logger.warning("Failed to refresh result attachments: %s", exc)
        dest = None

    if turn_index is not None:
        _persist_turn_result_snapshot(
            layout,
            turn_index=turn_index,
            summary=summary,
            figure_name=figure_name,
            figure_bytes=figure_bytes,
            figure_source=dest,
        )
    return dest


def _persist_turn_result_snapshot(
    layout: RunLayout,
    *,
    turn_index: int,
    summary: str,
    figure_name: str | None,
    figure_bytes: bytes | None,
    figure_source: Path | None = None,
) -> None:
    """Publish one result turn atomically and never replace prior evidence."""
    target = layout.turn_result_dir(turn_index)
    if target.exists():
        logger.warning("Refusing to overwrite existing result turn snapshot %s", target)
        return
    layout.result_turns_dir.mkdir(parents=True, exist_ok=True)
    staging = layout.result_turns_dir / f".{target.name}-{uuid4().hex}.tmp"
    try:
        (staging / "attachments").mkdir(parents=True)
        (staging / "summary.txt").write_text(summary or "", encoding="utf-8")
        if figure_name is not None and figure_bytes is not None:
            _link_or_write_attachment(
                figure_source,
                figure_bytes,
                staging / "attachments" / figure_name,
            )
        staging.replace(target)
    except OSError as exc:
        logger.warning("Failed to persist result turn snapshot %s: %s", target, exc)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _ensure_turn_result_snapshot(
    layout: RunLayout,
    *,
    turn_index: int,
    final_result: dict[str, Any],
    last_figure_path: str | None,
) -> None:
    """Ensure interrupted/failed turns also retain a non-overwriting result record."""
    if layout.turn_result_dir(turn_index).exists():
        return
    figure = _select_figure(
        final_result.get("attachments"),
        last_figure_path,
        layout.artifacts_dir,
    )
    figure_bytes: bytes | None = None
    figure_name: str | None = None
    if figure is not None:
        try:
            figure_bytes = figure.read_bytes()
            figure_name = f"figure{figure.suffix.lower()}"
        except OSError as exc:
            logger.warning("Failed to read turn figure %s: %s", figure, exc)
    summary = final_result.get("summary", "")
    _persist_turn_result_snapshot(
        layout,
        turn_index=turn_index,
        summary=summary if isinstance(summary, str) else str(summary),
        figure_name=figure_name,
        figure_bytes=figure_bytes,
        figure_source=figure,
    )


def _link_or_write_attachment(source: Path | None, payload: bytes, target: Path) -> None:
    """Preserve a self-contained result path without duplicating immutable bytes."""
    if source is not None and source.is_file():
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    target.write_bytes(payload)


def _persist_run_trace(
    output_dir: Path,
    task: str,
    result: AgentResult,
    config: AgentConfig,
    *,
    run_id: str | None = None,
    resume_count: int = 0,
    resumed: bool = False,
    turn_index: int | None = None,
    turn_start_step: int = 1,
    planner_attempt_start: int = 0,
    event_start: int = 0,
) -> None:
    """Persist an auditable, screenshot-free execution trace."""
    run_id = run_id or str(uuid4())
    layout = RunLayout.from_root(output_dir)
    anti_shortcut_configured = bool(
        config.strict_eval_mode
        and config.search_engine_only
        and config.browser_profile_mode == "temporary"
        and config.browser_channel == "bundled"
        and not config.persistent_pdf_cache
    )
    trace_payload = {
        "run_id": run_id,
        "run_kind": "agent_e2e",
        "resume_count": resume_count,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION if resumed else None,
        "resumed_from_checkpoint": resumed,
        "task": task,
        "status": result.status,
        "success": result.success,
        "steps_taken": sum(step.step_number >= turn_start_step for step in result.history),
        "total_duration": result.total_duration,
        "final_result": _portable_trace_final_result(result.final_result, layout),
        "evaluation": {
            "agent_source_sha256": package_source_fingerprint(),
            "mode": (
                "search_engine_only"
                if config.search_engine_only
                else (
                    "hybrid_api_augmented"
                    if config.discovery_mode == "hybrid"
                    else "browser_grounded"
                )
            ),
            "discovery_mode": config.discovery_mode,
            "direct_source_tools_enabled": config.discovery_mode == "hybrid",
            "high_risk_action_policy": config.high_risk_action_policy,
            "stealth_mode": config.stealth_mode,
            "anti_shortcut_contract": (
                "search_engine_only_v8" if anti_shortcut_configured else None
            ),
            "certificate_required": config.strict_eval_mode,
            "strict_eval_mode": config.strict_eval_mode,
            "search_engine_only": config.search_engine_only,
            "browser_profile_mode": config.browser_profile_mode,
            "browser_channel": config.browser_channel,
            "persistent_pdf_cache": config.persistent_pdf_cache,
        },
        "planner_attempts": [
            attempt.model_dump(mode="json")
            for attempt in result.planner_attempts[planner_attempt_start:]
        ],
        "events": _trace_value(result.events[event_start:]),
        "steps": [
            {
                "step_number": step.step_number,
                "run_id": run_id,
                "timestamp": step.timestamp,
                "tool": step.tool_call.tool_name,
                "parameters": _trace_parameters(step.tool_call),
                "reasoning": step.tool_call.reasoning,
                "success": step.tool_result.success,
                "error": step.tool_result.error,
                "result": _trace_value(
                    planner_context(step.tool_call.tool_name, step.tool_result.data)
                ),
                "planner_visible_result": planner_result_preview(
                    step.tool_call.tool_name,
                    _portable_run_paths(step.tool_result.data, layout),
                    success=step.tool_result.success,
                ),
                "policy": _trace_value(step.tool_result.audit),
                "duration_seconds": step.duration_seconds,
                "tool_duration_seconds": step.tool_duration_seconds,
            }
            for step in result.history
            if step.step_number >= turn_start_step
        ],
    }
    trace_payload = _portable_run_paths(trace_payload, layout)
    path = layout.trace_path
    temporary = path.with_suffix(".json.tmp")
    turn_temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        trace = build_run_trace_v8(trace_payload)
        encoded_trace = json.dumps(trace, ensure_ascii=False, indent=2).encode("utf-8")
        temporary.write_bytes(encoded_trace)
        temporary.replace(path)
        if config.strict_eval_mode:
            from webagent.evaluation.trace_verifier import write_verification_certificate

            write_verification_certificate(path)
        if turn_index is not None:
            turn_path = layout.turn_trace_path(turn_index)
            if turn_path.exists():
                raise FileExistsError(f"turn trace snapshot already exists: {turn_path}")
            turn_path.parent.mkdir(parents=True, exist_ok=True)
            turn_trace = _trace_for_turn_snapshot(trace, layout, turn_index)
            encoded_turn = json.dumps(turn_trace, ensure_ascii=False, indent=2).encode("utf-8")
            if encoded_turn == encoded_trace:
                os.link(path, turn_path)
            else:
                turn_temporary = turn_path.with_name(f".{turn_path.name}-{uuid4().hex}.tmp")
                turn_temporary.write_bytes(encoded_turn)
                turn_temporary.replace(turn_path)
    except (OSError, TypeError, ValueError) as exc:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        if turn_temporary is not None:
            with suppress(OSError):
                turn_temporary.unlink(missing_ok=True)
        logger.warning("Failed to write trajectory/trace.json: %s", exc)
        if config.strict_eval_mode:
            raise TracePersistenceError(
                "strict evaluation failed to persist trace.json and verification.json"
            ) from exc


def _trace_for_turn_snapshot(
    trace: dict[str, Any], layout: RunLayout, turn_index: int
) -> dict[str, Any]:
    """Bind historical attachments to their immutable result-turn copies."""
    snapshot = deepcopy(trace)
    final_result = snapshot.get("final_result")
    if not isinstance(final_result, dict):
        return snapshot
    attachments = final_result.get("attachments")
    if not isinstance(attachments, list):
        return snapshot
    canonical_root = layout.attachments_dir.resolve()
    immutable_root = layout.turn_attachments_dir(turn_index)
    rewritten: list[Any] = []
    for attachment in attachments:
        if not isinstance(attachment, str):
            rewritten.append(attachment)
            continue
        if attachment.startswith(("http://", "https://")):
            rewritten.append(attachment)
            continue
        candidate = Path(attachment).expanduser()
        path = (candidate if candidate.is_absolute() else layout.root / candidate).resolve()
        if path.parent == canonical_root:
            immutable = immutable_root / path.name
            if immutable.is_file():
                rewritten.append(immutable.relative_to(layout.root).as_posix())
                continue
        rewritten.append(attachment)
    final_result["attachments"] = rewritten
    return snapshot


def _portable_trace_final_result(value: Any, layout: RunLayout) -> Any:
    """Redact the final result and store run-contained attachments as relative paths."""
    portable = _trace_value(value)
    if not isinstance(portable, dict):
        return portable
    attachments = portable.get("attachments")
    if not isinstance(attachments, list):
        return portable
    rewritten: list[Any] = []
    for attachment in attachments:
        if not isinstance(attachment, str) or attachment.startswith(("http://", "https://")):
            rewritten.append(attachment)
            continue
        candidate = Path(attachment).expanduser()
        resolved = (candidate if candidate.is_absolute() else layout.root / candidate).resolve()
        if resolved == layout.root or resolved.is_relative_to(layout.root):
            rewritten.append(resolved.relative_to(layout.root).as_posix())
        else:
            rewritten.append(attachment)
    portable["attachments"] = rewritten
    return portable


def _portable_run_paths(value: Any, layout: RunLayout) -> Any:
    """Rewrite absolute paths contained by a run so frozen evidence can be moved."""
    if isinstance(value, dict):
        return {str(key): _portable_run_paths(item, layout) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_run_paths(item, layout) for item in value]
    if not isinstance(value, str) or not Path(value).is_absolute():
        return value
    try:
        resolved = Path(value).expanduser().resolve()
        if resolved == layout.root or resolved.is_relative_to(layout.root):
            return resolved.relative_to(layout.root).as_posix()
    except OSError:
        return value
    return value


def _trace_value(value: Any, key: str = "") -> Any:
    """Bound trace size and redact common secret/binary fields."""
    if key.casefold() in {"api_key", "token", "password", "base64", "data_url", "image"}:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _trace_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_trace_value(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 5000:
        return value[:5000] + "...[truncated]"
    return value


def _trace_parameters(tool_call: ToolCall) -> dict[str, Any]:
    """Redact semantically sensitive values even when their key is merely ``text``."""
    parameters = _trace_value(tool_call.parameters)
    if not isinstance(parameters, dict):
        return {}
    assessment = assess_tool_call(tool_call)
    if assessment.external_effect == "sensitive_input" and "text" in parameters:
        parameters["text"] = "[redacted]"
    if assessment.external_effect == "local_file_disclosure" and "path" in parameters:
        parameters["path"] = "[redacted]"
    return parameters
