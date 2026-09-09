"""Main agent loop: observe → think → act → record."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from webagent.agent.checkpoint import (
    AgentCheckpoint,
    BrowserResumeState,
    CheckpointStore,
    PendingAction,
    ReplayPolicy,
    checkpoint_fingerprint,
)
from webagent.agent.checkpoint_redaction import (
    _checkpoint_artifacts,
    _checkpoint_local_path,
    _checkpoint_loop_state,
    _checkpoint_planner_attempt,
    _checkpoint_planning_state,
    _checkpoint_step,
    _checkpoint_strategy_state,
    _checkpoint_tab_url,
    _checkpoint_value,
    _config_fingerprint,
)
from webagent.agent.context import planner_result_preview
from webagent.agent.evidence_store import record_event, save_result
from webagent.agent.history import SessionHistory
from webagent.agent.loop_detector import LoopDetector
from webagent.agent.loop_state import LoopState as _LoopState
from webagent.agent.observations import save_observation
from webagent.agent.planning import PlanningCoordinator
from webagent.agent.run_outputs import (
    _FIGURE_TOOLS,
    _attach_figure,
    _ensure_turn_result_snapshot,
    _persist_final_outputs,
    _persist_run_trace,
    _save_step_screenshot,
    _select_figure,
)
from webagent.agent.run_outputs import TracePersistenceError as TracePersistenceError
from webagent.agent.state import PlanningState
from webagent.agent.strategy import (
    StrategyManager,
    StrategyObservation,
    StrategyUpdate,
)
from webagent.browser.capture_failure import InconsistentCapture, failed_observation
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

        self._planning = PlanningCoordinator(self)

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
            self._reset_session_history()

        self._current_task = task
        self._task_status = TaskStatus.RUNNING

        state = await self._initialize_turn(task, follow_up=follow_up, resume_from=resume_from)

        turn_start_step = state.next_step if follow_up else 1
        turn_planner_attempt_start = len(self._planner_attempts) if follow_up else 0
        turn_event_start = len(self._runtime_events) if follow_up else 0

        for hook in self._hooks:
            await hook.on_task_start(task)

        step_count = state.next_step - 1
        try:
            if self._resolve_pending_action(state):
                step_limit = state.next_step + max_steps - 1 if follow_up else max_steps
                step_count = await self._run_steps(state, step_limit)
        finally:
            self._preserve_unfinished_result(state)
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

    def _preserve_unfinished_result(self, state: _LoopState) -> None:
        if self._task_status == TaskStatus.COMPLETED or state.final_result.get("summary"):
            return
        from webagent.agent.termination import unfinished_result

        steps = self._history.steps
        policy = next((s.tool_result.audit for s in reversed(steps) if s.tool_result.audit), {})
        error = next((s.tool_result.error for s in reversed(steps) if s.tool_result.error), None)
        state.final_result = {
            **state.final_result,
            **unfinished_result(self._task_status.value, policy, self._runtime_events, error),
        }
        _persist_final_outputs(
            self.output_dir,
            state.final_result["summary"],
            _select_figure(None, state.last_figure_path, self.run_layout.artifacts_dir),
            turn_index=self._session_turn_index,
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

    def _reset_session_history(self) -> None:
        """Clear all per-session evidence and loop signals before a fresh task."""
        self._history.clear()
        self._planner_attempts.clear()
        self._runtime_events.clear()
        self._session_run_id = None
        self._session_turn_index = 0
        # The detector persists recent_actions/url_history across runs; reset
        # it so a second task on the same instance can't fire a false loop nudge.
        if self.loop_detector is not None:
            self.loop_detector.reset()

    def _resolve_pending_action(self, state: _LoopState) -> bool:
        """Block ambiguous side effects; discard safe pending work before re-observing."""
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
            return False
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
        return True

    async def _initialize_turn(
        self,
        task: str,
        *,
        follow_up: bool,
        resume_from: str | Path | None,
    ) -> _LoopState:
        """Prepare a fresh turn, reuse the owned session, or validate a checkpoint."""
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

        return state

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

    async def _observe_after_action(
        self,
        tool_call: ToolCall,
        browser_state: BrowserState,
    ) -> BrowserState:
        """Wait before observing a changed page; reuse the observation for done."""
        # ``done`` is side-effect free for the browser. Reusing the pre-action
        # state avoids a redundant DOM/screenshot round trip on every success.
        if tool_call.tool_name == "done":
            return browser_state.model_copy(
                update={
                    "observation_metadata": {
                        **browser_state.observation_metadata,
                        "status": "reused",
                        "reused_observation_id": browser_state.observation_id,
                    }
                }
            )
        # This delay belongs before the post-action observation. Previously
        # it ran after the screenshot was already persisted, so it could not
        # prevent captures of half-loaded pages.
        if self.config.post_action_wait_ms > 0:
            await asyncio.sleep(self.config.post_action_wait_ms / 1000)
        return await self._observe()

    async def _execute_step(self, step_count: int, state: _LoopState) -> bool:
        """Run one observe → think → act → record step."""
        step_start = time.time()
        state.next_step = step_count
        if self._timed_out(state):
            self._task_status = TaskStatus.TIMEOUT
            return False
        browser_state = await self._observe_step_state(state)
        if browser_state is None:
            return False
        observation_path = save_observation(browser_state, self.run_layout, step_count, "pre")
        record_event(
            self.run_layout.root, "observed", step_count, observation_path=observation_path
        )
        browser_state = browser_state.model_copy(update={"observation_path": observation_path})
        recorder = getattr(self._tool_executor, "record_observation", None)
        if callable(recorder):
            recorder(browser_state)
        self._active_loop_state = state
        tool_call = await self._think(browser_state, step_count)
        if tool_call is None:
            return await self._handle_missing_tool(step_count, state, browser_state)
        return await self._execute_tool_call(
            step_count, state, step_start, browser_state, tool_call
        )

    async def _observe_step_state(self, state: _LoopState) -> BrowserState | None:
        browser_state = await self._observe()
        if not self._captcha_pause:
            return browser_state
        challenge_state = await self._handle_captcha()
        if challenge_state == "blocked":
            self._task_status = TaskStatus.BLOCKED
            await self._save_checkpoint(state, status=self._task_status.value)
            return None
        if challenge_state == "resolved":
            return await self._observe()
        return browser_state

    async def _handle_missing_tool(
        self, step_count: int, state: _LoopState, browser_state: BrowserState
    ) -> bool:
        record_event(self.run_layout.root, "planning_failed", step_count)
        save_observation(
            browser_state.model_copy(
                update={
                    "screenshot": None,
                    "full_page_screenshot": None,
                    "viewport_context": None,
                    "document_context": None,
                    "viewport_blocks": [],
                    "document_blocks": [],
                    "elements": [],
                    "observation_id": None,
                    "dom_summary": "(action not attempted: planning failed)",
                    "observation_metadata": {
                        "status": "not_attempted",
                        "reason": "planning_failed",
                    },
                }
            ),
            self.run_layout,
            step_count,
            "post",
        )
        _save_step_screenshot(browser_state, self._step_screenshot_path(step_count))
        state.consecutive_failures += 1
        state.next_step = step_count + 1
        await self._save_checkpoint(state, status="running")
        if state.consecutive_failures >= self.config.max_consecutive_failures:
            self._task_status = TaskStatus.FAILED
            return False
        return True

    async def _execute_tool_call(
        self,
        step_count: int,
        state: _LoopState,
        step_start: float,
        browser_state: BrowserState,
        tool_call: ToolCall,
    ) -> bool:
        assessment = assess_tool_call(tool_call)
        state.pending_action = PendingAction(
            tool_name=tool_call.tool_name,
            parameters_sha256=checkpoint_fingerprint(tool_call.parameters),
            external_effect=assessment.external_effect,
            replay_policy=_replay_policy(tool_call, assessment.approval_required),
        )
        await self._save_checkpoint(state, status="running")
        tool_start = time.time()
        record_event(
            self.run_layout.root,
            "action_started",
            step_count,
            tool=tool_call.tool_name,
            parameters_sha256=checkpoint_fingerprint(tool_call.parameters),
        )
        tool_result = await self._act(tool_call)
        record_event(
            self.run_layout.root,
            "action_finished",
            step_count,
            tool=tool_call.tool_name,
            success=tool_result.success,
            result_ref=save_result(self.run_layout.root, tool_result.data),
        )
        tool_duration = time.time() - tool_start
        post_action_state = await self._observe_after_action(tool_call, browser_state)
        save_observation(
            post_action_state.model_copy(
                update={"observation_path": browser_state.observation_path}
            ),
            self.run_layout,
            step_count,
            "post",
        )
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
        return self._finish_tool_step(state, tool_call, tool_result)

    def _finish_tool_step(
        self, state: _LoopState, tool_call: ToolCall, tool_result: ToolResult
    ) -> bool:
        if self._timed_out(state):
            self._task_status = TaskStatus.TIMEOUT
            return False
        if tool_call.tool_name == "done" and tool_result.success:
            self._task_status = (
                TaskStatus.BLOCKED
                if tool_result.data.get("completion_status") == "partial"
                else TaskStatus.COMPLETED
            )
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
        attempts: list[dict[str, Any]] = []
        partial: InconsistentCapture | None = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                async with asyncio.timeout(self.config.observation_capture_timeout_seconds):
                    snapshot = await self._capture_snapshot()
                dom_summary = snapshot.get("markdown", "")
                snapshot["meta"]["capture_attempts"] = [
                    *attempts,
                    {
                        "attempt": attempt + 1,
                        "status": "complete",
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    },
                ]
                screenshot = None
                raw = snapshot.get("screenshot_bytes")
                if raw:
                    try:
                        screenshot = Image.open(BytesIO(raw))
                    except Exception:
                        pass
                return BrowserState(
                    screenshot=screenshot,
                    full_page_screenshot=_snapshot_image(
                        snapshot.get("full_page_screenshot_bytes")
                    ),
                    observation_metadata=snapshot["meta"],
                    elements=snapshot.get("elements", []),
                    observation_id=snapshot["meta"].get("observation_id"),
                    viewport_context=snapshot.get("viewport_context"),
                    document_context=snapshot.get("document_context"),
                    viewport_blocks=snapshot.get("viewport_blocks", []),
                    document_blocks=snapshot.get("document_blocks", []),
                    dom_summary=dom_summary,
                    url=snapshot["meta"].get("url", ""),
                    title=snapshot["meta"].get("title", ""),
                    timestamp=snapshot["meta"].get("timestamp", datetime.now(UTC).isoformat()),
                )
            except Exception as e:
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "error_type": type(e).__name__,
                        "reason": str(e)
                        if isinstance(e, InconsistentCapture)
                        else "capture stage raised; see exception type and timing",
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "phase": "consistency" if isinstance(e, InconsistentCapture) else "capture",
                    }
                )
                if isinstance(e, InconsistentCapture):
                    partial = e
                logger.warning("Observe attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)
        return await failed_observation(
            self._browser.page,
            attempts,
            partial,
            timeout_seconds=self.config.observation_fallback_timeout_seconds,
        )

    async def _capture_snapshot(self) -> dict[str, Any]:
        try:
            await self._browser.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await wait_for_page_stability(
            self._browser.page,
            timeout_ms=self.config.observation_stability_timeout_ms,
            stable_ms=self.config.observation_stable_ms,
        )
        return await take_snapshot(
            self._browser.page,
            task=self._current_task,
            use_cdp=self.config.use_cdp,
            max_elements=self.config.max_snapshot_elements,
            filter_ads=self.config.enable_ad_filtering,
            wait_after_load=0,
            supplemental_full_page=self.config.observation_full_page_screenshot,
            viewport_chars=self.config.observation_viewport_chars,
            document_chars=self.config.observation_document_chars,
            max_dom_nodes=self.config.observation_max_dom_nodes,
            max_text_chars=self.config.observation_max_text_chars,
            text_share=self.config.observation_text_share,
            text_block_chars=self.config.observation_text_block_chars,
        )

    async def _check_for_captcha(self) -> dict[str, Any]:
        """Check if current page has a captcha challenge.

        Returns:
            Dictionary with captcha detection results.
        """
        return await self._browser.check_captcha()

    async def _think(
        self, browser_state: BrowserState, step_number: int = 0, state: _LoopState | None = None
    ) -> ToolCall | None:
        return await self._planning.plan_action(browser_state, step_number, state)

    async def _elicit_terminal_confidence(self, state: _LoopState, step_count: int) -> None:
        await self._planning.elicit_terminal_confidence(state, step_count)

    async def _act(self, tool_call: ToolCall) -> ToolResult:
        return await self._tool_executor.execute(tool_call)


_is_blank_screenshot = is_blank_image


def _snapshot_image(raw: Any) -> Image.Image | None:
    if not isinstance(raw, bytes):
        return None
    try:
        return Image.open(BytesIO(raw))
    except Exception:
        return None


def _initial_planning_state(task: str) -> PlanningState:
    return PlanningState.create(
        task,
        [
            "Discover grounded candidate sources and required prerequisites",
            "Execute the requested browser or document workflow",
            "Verify the evidence and return an honest final result",
        ],
    )


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
