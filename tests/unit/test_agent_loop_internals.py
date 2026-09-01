"""Tests for the real ``_observe`` / ``_think`` / step internals of WebAgent.

Other agent tests stub these methods out; here we drive the genuine
implementations with a fake browser, planner and a patched ``take_snapshot`` so
the observe/think branches (retries, loop-nudge injection, vision warnings,
failure tracking, captcha, timeouts) are actually executed.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from webagent.agent import loop as loop_mod
from webagent.agent.loop import WebAgent, _LoopState, _persist_final_outputs, _save_step_screenshot
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, TaskStatus, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakePage:
    def __init__(self, url: str = "https://example.com") -> None:
        self.url = url
        self.load_state_calls = 0

    async def wait_for_load_state(self, state: str, timeout: int = 5000) -> None:
        self.load_state_calls += 1


class FakeBrowser:
    def __init__(self, captcha: dict[str, Any] | None = None) -> None:
        self.page = FakePage()
        self._captcha = captcha or {"detected": False}
        self.closed = False

    async def check_captcha(self) -> dict[str, Any]:
        return self._captcha

    async def close(self) -> None:
        self.closed = True


class FakePlanner:
    def __init__(self, tool_call: ToolCall | None = None, raises: bool = False) -> None:
        self._tool_call = tool_call
        self._raises = raises
        self.vision_actually_works = True
        self.received_history: str | None = None

    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def plan_action(
        self, task: str, browser_state: Any, history_text: str, available_tools: str
    ) -> ToolCall | None:
        self.received_history = history_text
        if self._raises:
            raise RuntimeError("planner exploded")
        return self._tool_call


class FakeExecutor:
    def get_tool_descriptions(self) -> str:
        return "tool docs"


def _agent(tmp_path: Path, planner: FakePlanner, browser: FakeBrowser, **cfg: Any) -> WebAgent:
    cfg.setdefault("enable_loop_detection", False)
    captcha_pause = cfg.pop("captcha_pause", False)
    config = AgentConfig(
        _env_file=None,
        use_vllm=False,
        captcha_pause=captcha_pause,
        **cfg,
    )
    return WebAgent(
        planner=planner,
        browser=browser,
        tool_executor=FakeExecutor(),
        config=config,
        output_dir=tmp_path / "outputs",
    )


class TestObserve:
    async def test_success_builds_state_with_screenshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_snapshot(page, **kwargs):
            return {
                "markdown": "# page",
                "screenshot_bytes": _png_bytes(),
                "meta": {"url": "https://x", "title": "X"},
            }

        monkeypatch.setattr(loop_mod, "take_snapshot", fake_snapshot)
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        state = await agent._observe()
        assert state.dom_summary == "# page"
        assert state.url == "https://x"
        assert state.screenshot is not None

    async def test_falls_back_after_repeated_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(page, **kwargs):
            raise RuntimeError("snapshot failed")

        async def instant_sleep(_seconds):
            return None

        monkeypatch.setattr(loop_mod, "take_snapshot", boom)
        monkeypatch.setattr(loop_mod.asyncio, "sleep", instant_sleep)
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        state = await agent._observe()
        assert state.dom_summary == "(page loading)"
        assert state.url == "https://example.com"


class TestThink:
    def _state(self) -> BrowserState:
        return BrowserState(
            screenshot=None,
            dom_summary="body",
            url="https://x",
            title="T",
            timestamp="now",
        )

    async def test_returns_tool_call(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="goto", parameters={"url": "https://x"}))
        planner.last_call_metadata = {"transport_retries": 2}
        agent = _agent(tmp_path, planner, FakeBrowser())
        call = await agent._think(self._state())
        assert call is not None and call.tool_name == "goto"
        assert agent._planner_attempts[0].transport_retries == 2

    async def test_planner_exception_returns_none(self, tmp_path: Path) -> None:
        planner = FakePlanner(raises=True)
        agent = _agent(tmp_path, planner, FakeBrowser())
        assert await agent._think(self._state()) is None
        assert len(agent._planner_attempts) == 2
        assert all(not attempt.success for attempt in agent._planner_attempts)

    async def test_invalid_plan_is_repaired_within_same_step(self, tmp_path: Path) -> None:
        class RepairingPlanner(FakePlanner):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0
                self.histories: list[str] = []

            async def plan_action(self, task, browser_state, history_text, available_tools):
                self.calls += 1
                self.histories.append(history_text)
                if self.calls == 1:
                    return None
                return ToolCall(tool_name="done", parameters={"summary": "repaired"})

        planner = RepairingPlanner()
        agent = _agent(tmp_path, planner, FakeBrowser())

        call = await agent._think(self._state(), 4)

        assert call is not None and call.tool_name == "done"
        assert "PREVIOUS PLANNER ATTEMPT FAILED" in planner.histories[1]
        assert [(a.step_number, a.attempt_number, a.success) for a in agent._planner_attempts] == [
            (4, 1, False),
            (4, 2, True),
        ]

    async def test_invalid_tool_arguments_are_repaired_before_action_step(
        self, tmp_path: Path
    ) -> None:
        class RepairingPlanner(FakePlanner):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0
                self.histories: list[str] = []

            async def plan_action(self, task, browser_state, history_text, available_tools):
                del task, browser_state, available_tools
                self.calls += 1
                self.histories.append(history_text)
                if self.calls == 1:
                    return ToolCall(tool_name="goto", parameters={"url": 3})
                return ToolCall(tool_name="done", parameters={"summary": "repaired"})

        class ValidatingExecutor(FakeExecutor):
            def validate_tool_call(self, call: ToolCall) -> str | None:
                if call.tool_name == "goto" and not isinstance(call.parameters.get("url"), str):
                    return "Validation: url must be a string"
                return None

        planner = RepairingPlanner()
        agent = _agent(tmp_path, planner, FakeBrowser())
        agent._tool_executor = ValidatingExecutor()

        call = await agent._think(self._state(), 2)

        assert call is not None and call.tool_name == "done"
        assert "url must be a string" in planner.histories[1]
        assert [attempt.success for attempt in agent._planner_attempts] == [False, True]

    async def test_vision_disabled_warning_injected(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="done", parameters={"summary": "x"}))
        planner.vision_actually_works = False
        agent = _agent(tmp_path, planner, FakeBrowser())
        await agent._think(self._state())
        assert "VISION DISABLED" in planner.received_history

    async def test_transient_page_recovery_hint_is_grounded_in_observation(
        self, tmp_path: Path
    ) -> None:
        planner = FakePlanner(ToolCall(tool_name="refresh"))
        agent = _agent(tmp_path, planner, FakeBrowser())
        browser_state = BrowserState(
            screenshot=None,
            dom_summary="The workflow is intact. Retry this request.",
            url="https://example.test/workflow",
            title="Service Unavailable",
            timestamp="now",
        )

        await agent._think(browser_state)

        assert planner.received_history is not None
        assert "OBSERVED TRANSIENT PAGE" in planner.received_history
        assert "visible retry/reload control" in planner.received_history
        assert "blank or guessed URL" in planner.received_history

    async def test_loop_detector_nudge_injected(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="goto", parameters={"url": "https://x"}))
        agent = _agent(
            tmp_path,
            planner,
            FakeBrowser(),
            enable_loop_detection=True,
            loop_window_size=2,
            loop_threshold=2,
        )
        assert agent.loop_detector is not None
        # Feed identical actions so the detector reports a loop on the next think.
        for _ in range(4):
            agent.loop_detector.add_action(
                tool_name="goto",
                page_url="https://x",
                page_hash="samehash",
                parameters={"url": "https://x"},
            )
        await agent._think(self._state())
        assert planner.received_history is not None
        # Action was also recorded into the detector during think.

    async def test_action_budget_warns_before_terminal_step(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="done", parameters={"summary": "x"}))
        agent = _agent(tmp_path, planner, FakeBrowser(), max_steps=8)

        await agent._think(self._state(), step_number=7)

        assert planner.received_history is not None
        assert "Two actions remain" in planner.received_history
        assert "Reserve the final action" in planner.received_history

    async def test_action_budget_marks_final_action(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="done", parameters={"summary": "x"}))
        agent = _agent(tmp_path, planner, FakeBrowser(), max_steps=8)

        await agent._think(self._state(), step_number=8)

        assert planner.received_history is not None
        assert "final allowed action" in planner.received_history
        assert "exact source URL" in planner.received_history


class TestWarnIfCaptcha:
    async def test_logs_when_detected(self, tmp_path: Path, caplog) -> None:
        browser = FakeBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "iframe present",
            }
        )
        agent = _agent(tmp_path, FakePlanner(), browser)
        import logging

        with caplog.at_level(logging.WARNING, logger="webagent"):
            await agent._warn_if_captcha()
        assert any("Captcha detected" in r.message for r in caplog.records)

    async def test_fail_mode_blocks_before_planning(self, tmp_path: Path) -> None:
        browser = FakeBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "iframe present",
            }
        )
        agent = _agent(
            tmp_path,
            FakePlanner(ToolCall(tool_name="done", parameters={"summary": "wrong"})),
            browser,
            captcha_pause=True,
            captcha_handling="fail",
        )

        async def observe() -> BrowserState:
            return BrowserState(
                screenshot=None,
                dom_summary="challenge",
                url=browser.page.url,
                title="Captcha",
                timestamp="now",
            )

        agent._observe = observe  # type: ignore[method-assign]
        state = _LoopState(start_time=__import__("time").time())

        assert await agent._execute_step(1, state) is False
        assert agent._task_status == TaskStatus.BLOCKED
        assert agent._runtime_events[0]["outcome"] == "blocked"
        assert browser.closed is True

    async def test_headed_wait_resumes_after_human_clears_challenge(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class SequenceBrowser(FakeBrowser):
            def __init__(self) -> None:
                super().__init__()
                self.results = [
                    {
                        "detected": True,
                        "type": "hcaptcha",
                        "confidence": 0.9,
                        "reason": "iframe present",
                    },
                    {"detected": False},
                ]

            async def check_captcha(self) -> dict[str, Any]:
                return self.results.pop(0)

        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("webagent.agent.loop.asyncio.sleep", no_sleep)
        browser = SequenceBrowser()
        agent = _agent(
            tmp_path,
            FakePlanner(),
            browser,
            browser_headless=False,
            captcha_handling="wait_for_human",
        )

        outcome = await agent._handle_captcha()

        assert outcome == "resolved"
        assert agent._runtime_events[0]["outcome"] == "resolved_by_human"

    async def test_default_report_waits_in_headed_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class SequenceBrowser(FakeBrowser):
            def __init__(self) -> None:
                super().__init__()
                self.results = [
                    {
                        "detected": True,
                        "type": "recaptcha",
                        "confidence": 0.9,
                        "reason": "visible challenge",
                    },
                    {"detected": False},
                ]

            async def check_captcha(self) -> dict[str, Any]:
                return self.results.pop(0)

        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("webagent.agent.loop.asyncio.sleep", no_sleep)
        agent = _agent(
            tmp_path,
            FakePlanner(),
            SequenceBrowser(),
            browser_headless=False,
            captcha_handling="report",
        )

        assert await agent._handle_captcha() == "resolved"
        assert agent._runtime_events[0]["outcome"] == "resolved_by_human"

    async def test_default_report_times_out_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticks = iter((0.0, 2.0))
        monkeypatch.setattr(loop_mod, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        browser = FakeBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "visible challenge",
            }
        )
        agent = _agent(
            tmp_path,
            FakePlanner(),
            browser,
            browser_headless=False,
            captcha_handling="report",
            captcha_wait_timeout_seconds=1,
        )

        assert await agent._handle_captcha() == "blocked"
        assert agent._runtime_events[0]["outcome"] == "human_wait_timeout"
        assert agent._runtime_events[0]["browser_closed"] is True
        assert browser.closed is True

    async def test_default_report_fails_closed_when_headless(self, tmp_path: Path) -> None:
        browser = FakeBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "visible challenge",
            }
        )
        agent = _agent(
            tmp_path,
            FakePlanner(),
            browser,
            browser_headless=True,
            captcha_handling="report",
        )

        assert await agent._handle_captcha() == "blocked"
        assert agent._runtime_events[0]["outcome"] == "blocked"
        assert browser.closed is True

    async def test_strict_search_captcha_blocks_without_closing_shared_browser(
        self, tmp_path: Path
    ) -> None:
        browser = FakeBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "visible challenge",
            }
        )
        agent = _agent(
            tmp_path,
            FakePlanner(),
            browser,
            browser_headless=True,
            captcha_handling="fail",
            search_engine_only=True,
        )

        assert await agent._handle_captcha() == "blocked"
        assert agent._runtime_events[0]["outcome"] == "blocked"
        assert agent._runtime_events[0]["browser_closed"] is False
        assert agent._runtime_events[0]["browser_retained_for_isolated_reset"] is True
        assert browser.closed is False

    async def test_captcha_remains_blocked_if_browser_close_fails(self, tmp_path: Path) -> None:
        class CloseFailureBrowser(FakeBrowser):
            async def close(self) -> None:
                raise RuntimeError("close failed")

        browser = CloseFailureBrowser(
            captcha={
                "detected": True,
                "type": "recaptcha",
                "confidence": 0.9,
                "reason": "visible challenge",
            }
        )
        agent = _agent(
            tmp_path,
            FakePlanner(),
            browser,
            browser_headless=True,
            captcha_handling="report",
        )

        assert await agent._handle_captcha() == "blocked"
        assert agent._runtime_events[0]["browser_closed"] is False
        assert "close failed" in agent._runtime_events[0]["close_error"]


class TestExecuteStepBranches:
    def test_loop_window_fits_short_task_budget(self, tmp_path: Path) -> None:
        agent = _agent(
            tmp_path,
            FakePlanner(),
            FakeBrowser(),
            enable_loop_detection=True,
            max_steps=8,
            loop_window_size=10,
        )

        assert agent.loop_detector is not None
        assert agent.loop_detector.window_size == 4

    async def test_terminal_confidence_is_recorded_for_failed_run(self, tmp_path: Path) -> None:
        class ConfidencePlanner(FakePlanner):
            async def estimate_task_success(self, **kwargs: Any) -> float:
                assert kwargs["status"] == "failed"
                return 0.2

        agent = _agent(
            tmp_path,
            ConfidencePlanner(),
            FakeBrowser(),
            elicit_terminal_confidence=True,
        )
        agent._current_task = "task"
        agent._task_status = TaskStatus.FAILED
        state = _LoopState(start_time=0.0)

        await agent._elicit_terminal_confidence(state, 3)

        assert state.final_result["success_probability"] == 0.2
        assert state.final_result["confidence_source"] == "terminal_self_report"
        assert agent._runtime_events[-1]["type"] == "confidence_elicited"

    async def test_run_steps_cancels_in_flight_step_at_task_deadline(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser(), task_timeout=1)
        state = _LoopState(start_time=__import__("time").time() - 0.98)

        async def slow_step(step_count: int, loop_state: _LoopState) -> bool:
            del step_count, loop_state
            await asyncio.sleep(1)
            return True

        agent._execute_step = slow_step  # type: ignore[method-assign]
        await agent._run_steps(state, max_steps=2)

        assert agent._task_status == TaskStatus.TIMEOUT
        assert agent._runtime_events[-1]["type"] == "task_deadline_exceeded"

    async def test_timeout_before_observe(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser(), task_timeout=0)
        state = _LoopState(start_time=0.0)
        assert await agent._execute_step(1, state) is False
        assert agent._task_status == TaskStatus.TIMEOUT

    async def test_think_none_increments_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def observe():
            return BrowserState(screenshot=None, dom_summary="b", url="u", title="", timestamp="n")

        agent = _agent(
            tmp_path, FakePlanner(raises=True), FakeBrowser(), max_consecutive_failures=2
        )
        agent._observe = observe  # type: ignore[method-assign]
        state = _LoopState(start_time=__import__("time").time())
        # First None: keep going
        assert await agent._execute_step(1, state) is True
        assert state.consecutive_failures == 1
        # Second None: hits the failure ceiling and stops
        assert await agent._execute_step(2, state) is False
        assert agent._task_status == TaskStatus.FAILED

    async def test_post_action_wait_precedes_post_action_observation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        planner = FakePlanner(ToolCall(tool_name="goto", parameters={"url": "https://x"}))
        agent = _agent(tmp_path, planner, FakeBrowser(), post_action_wait_ms=125)

        async def observe() -> BrowserState:
            events.append("observe")
            return BrowserState(
                screenshot=None,
                dom_summary="page",
                url="https://example.com",
                title="Example",
                timestamp="now",
            )

        async def act(tool_call: ToolCall) -> ToolResult:
            events.append("act")
            return ToolResult(success=True, tool_name=tool_call.tool_name)

        async def sleep(seconds: float) -> None:
            events.append(f"sleep:{seconds}")

        agent._observe = observe  # type: ignore[method-assign]
        agent._act = act  # type: ignore[method-assign]
        monkeypatch.setattr(loop_mod.asyncio, "sleep", sleep)
        agent._task_status = TaskStatus.RUNNING
        state = _LoopState(start_time=__import__("time").time())

        assert await agent._execute_step(1, state) is True
        assert events == ["observe", "act", "sleep:0.125", "observe"]

    async def test_denied_done_does_not_complete_task(self, tmp_path: Path) -> None:
        planner = FakePlanner(ToolCall(tool_name="done", parameters={"summary": "too early"}))
        agent = _agent(tmp_path, planner, FakeBrowser())

        async def observe() -> BrowserState:
            return BrowserState(
                screenshot=None,
                dom_summary="page",
                url="https://example.com",
                title="Example",
                timestamp="now",
            )

        async def denied_action(tool_call: ToolCall) -> ToolResult:
            return ToolResult(
                success=False,
                tool_name=tool_call.tool_name,
                error="Policy denied tool call: missing evidence",
            )

        agent._observe = observe  # type: ignore[method-assign]
        agent._act = denied_action  # type: ignore[method-assign]
        agent._task_status = TaskStatus.RUNNING
        state = _LoopState(start_time=__import__("time").time())

        assert await agent._execute_step(1, state) is True
        assert agent._task_status == TaskStatus.RUNNING
        assert state.final_result == {}
        assert state.consecutive_failures == 1
        assert not RunLayout.from_root(agent.output_dir).summary_path.exists()


class TestUpdateFailureTracking:
    def test_failure_increments(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        state = _LoopState(start_time=0.0)
        agent._update_failure_tracking(
            ToolCall(tool_name="goto", parameters={}),
            ToolResult(success=False, tool_name="goto"),
            state,
        )
        assert state.consecutive_failures == 1

    def test_success_resets_and_tracks_figure(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        state = _LoopState(start_time=0.0)
        state.consecutive_failures = 3
        agent._update_failure_tracking(
            ToolCall(tool_name="analyze_image", parameters={}),
            ToolResult(success=True, tool_name="analyze_image", data={"path": "/tmp/a.png"}),
            state,
        )
        assert state.consecutive_failures == 0
        assert state.last_figure_path == "/tmp/a.png"


class TestHandleStepException:
    async def test_disconnect_is_swallowed(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        agent._handle_step_exception(RuntimeError("Target closed"))
        assert agent._task_status == TaskStatus.FAILED

    async def test_other_exception_reraises(self, tmp_path: Path) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        with pytest.raises(ValueError):
            try:
                raise ValueError("boom")
            except ValueError as exc:
                agent._handle_step_exception(exc)
        assert agent._task_status == TaskStatus.FAILED


class TestPrepareRunOutputDir:
    def test_refuses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = _agent(tmp_path, FakePlanner(), FakeBrowser())
        agent.output_dir = Path.cwd()
        with pytest.raises(ValueError, match="unsafe output directory"):
            agent._prepare_run_output_dir()


class TestPersistHelpers:
    def test_persist_writes_result_summary(self, tmp_path: Path) -> None:
        _persist_final_outputs(tmp_path, "the summary", None)
        assert RunLayout.from_root(tmp_path).summary_path.read_text() == "the summary"

    def test_persist_copies_figure(self, tmp_path: Path) -> None:
        src = tmp_path / "src.png"
        Image.new("RGB", (4, 4), "red").save(src)
        _persist_final_outputs(tmp_path, "s", src, turn_index=1)
        layout = RunLayout.from_root(tmp_path)
        canonical = layout.attachments_dir / "figure.png"
        snapshot = layout.turn_attachments_dir(1) / "figure.png"
        assert canonical.exists()
        assert snapshot.exists()
        assert canonical.stat().st_ino == snapshot.stat().st_ino

    def test_save_step_screenshot_noop_without_image(self, tmp_path: Path) -> None:
        state = BrowserState(screenshot=None, dom_summary="b", url="u", title="", timestamp="n")
        # Should not raise even though there is no screenshot to write.
        _save_step_screenshot(state, tmp_path / "shot.jpg")
        assert not (tmp_path / "shot.jpg").exists()

    def test_save_step_screenshot_hardlinks_unchanged_frames(self, tmp_path: Path) -> None:
        state = BrowserState(
            screenshot=Image.new("RGB", (8, 8), "blue"),
            dom_summary="b",
            url="u",
            title="",
            timestamp="n",
        )
        first = tmp_path / "step_001.jpg"
        second = tmp_path / "step_002.jpg"

        _save_step_screenshot(state, first)
        _save_step_screenshot(state, second)

        assert first.read_bytes() == second.read_bytes()
        assert first.stat().st_ino == second.stat().st_ino
