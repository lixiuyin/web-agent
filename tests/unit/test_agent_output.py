"""Tests for agent output directory handling."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from webagent.agent.loop import (
    TracePersistenceError,
    WebAgent,
    _as_image_path,
    _is_blank_screenshot,
    _persist_run_trace,
    _select_figure,
    _trace_parameters,
)
from webagent.core.config import AgentConfig
from webagent.core.models import AgentResult, BrowserState, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout, RunOwnershipError
from webagent.utils.images import is_blank_image


class _DummyPlanner:
    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def plan_action(self, *args, **kwargs):
        return None

    async def analyze_image(self, *args, **kwargs) -> str:
        return ""


class _DummyBrowser:
    pass


class _DummyExecutor:
    def get_tool_descriptions(self) -> str:
        return ""


def test_trace_parameters_redacts_sensitive_text_and_upload_path() -> None:
    password = _trace_parameters(
        ToolCall(
            tool_name="type",
            parameters={
                "selector": {"type": "css", "value": "#password"},
                "text": "orbit42",
            },
        )
    )
    upload = _trace_parameters(
        ToolCall(tool_name="upload_file", parameters={"path": "/private/identity.pdf"})
    )

    assert password["text"] == "[redacted]"
    assert upload["path"] == "[redacted]"


def test_non_strict_trace_persistence_remains_best_effort(tmp_path) -> None:
    result = AgentResult(
        success=True,
        status="completed",
        steps_taken=0,
        total_duration=0.1,
    )

    _persist_run_trace(
        tmp_path,
        "ordinary task",
        result,
        AgentConfig(_env_file=None, strict_eval_mode=False),
    )

    assert not RunLayout.from_root(tmp_path).trace_path.exists()


def test_strict_trace_write_failure_fails_closed(tmp_path) -> None:
    result = AgentResult(
        success=True,
        status="completed",
        steps_taken=0,
        total_duration=0.1,
    )
    config = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        search_engine_only=True,
        browser_profile_mode="temporary",
        persistent_pdf_cache=False,
    )

    with pytest.raises(TracePersistenceError, match="strict evaluation failed"):
        _persist_run_trace(tmp_path, "strict task", result, config)


def test_strict_certificate_failure_fails_closed(tmp_path, monkeypatch) -> None:
    from webagent.evaluation import trace_verifier

    layout = RunLayout.from_root(tmp_path)
    layout.trajectory_dir.mkdir()
    result = AgentResult(
        success=True,
        status="completed",
        steps_taken=0,
        total_duration=0.1,
    )
    config = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        search_engine_only=True,
        browser_profile_mode="temporary",
        persistent_pdf_cache=False,
    )

    def fail_certificate(_path):
        raise OSError("disk full")

    monkeypatch.setattr(trace_verifier, "write_verification_certificate", fail_certificate)

    with pytest.raises(TracePersistenceError, match="strict evaluation failed"):
        _persist_run_trace(tmp_path, "strict task", result, config)
    assert layout.trace_path.is_file()
    assert not layout.verification_path.exists()


async def test_agent_run_rejects_nonempty_unowned_output_dir(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("manual research notes", encoding="utf-8")

    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(_env_file=None, use_vllm=False, max_steps=1, captcha_pause=False),
        output_dir=output_dir,
    )

    async def observe() -> BrowserState:
        return BrowserState(
            screenshot=Image.new("RGB", (32, 24), "white"),
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="now",
        )

    async def think(browser_state: BrowserState, step_number: int = 0) -> ToolCall:
        return ToolCall(tool_name="done", parameters={"summary": "ok"})

    async def act(tool_call: ToolCall) -> ToolResult:
        return ToolResult(success=True, tool_name="done", data={"summary": "ok"})

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    with pytest.raises(RunOwnershipError, match="unowned"):
        await agent.run("task")

    assert sentinel.read_text(encoding="utf-8") == "manual research notes"


async def test_agent_run_cleans_owned_outputs_but_preserves_unknown_siblings(tmp_path):
    output_dir = tmp_path / "run"
    layout = RunLayout.from_root(output_dir)
    layout.prepare(run_id="old", task="old", model="old")
    stale_screenshot = layout.screenshots_dir / "step_001.jpg"
    stale_screenshot.write_bytes(b"old screenshot")
    unrelated_screenshot = layout.screenshots_dir / "manual.jpg"
    unrelated_screenshot.write_bytes(b"generated-tree content")
    notes = output_dir / "research-notes.md"
    notes.write_text("preserve", encoding="utf-8")

    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(_env_file=None, use_vllm=False, max_steps=1, captcha_pause=False),
        output_dir=output_dir,
    )

    async def observe() -> BrowserState:
        return BrowserState(
            screenshot=Image.new("RGB", (32, 24), "white"),
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="now",
        )

    async def think(browser_state: BrowserState, step_number: int = 0) -> ToolCall:
        return ToolCall(tool_name="done", parameters={"summary": "ok"})

    async def act(tool_call: ToolCall) -> ToolResult:
        return ToolResult(success=True, tool_name="done", data={"summary": "ok"})

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    result = await agent.run("task")

    assert result.status == "completed"
    assert notes.read_text(encoding="utf-8") == "preserve"
    assert not unrelated_screenshot.exists()
    assert layout.artifacts_dir.is_dir()
    assert layout.screenshots_dir.is_dir()
    screenshot = layout.screenshots_dir / "step_001.jpg"
    assert screenshot.exists()
    assert screenshot.read_bytes() != b"old screenshot"
    assert is_blank_image(Image.open(screenshot)) is True


async def test_agent_run_reports_async_cancellation_as_interrupted(tmp_path):
    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(_env_file=None, use_vllm=False, max_steps=1, captcha_pause=False),
        output_dir=tmp_path / "outputs",
    )

    async def cancelled_observe() -> BrowserState:
        raise asyncio.CancelledError

    agent._observe = cancelled_observe  # type: ignore[method-assign]

    result = await agent.run("task")

    assert result.status == "interrupted"
    assert result.success is False
    layout = RunLayout.from_root(tmp_path / "outputs")
    assert layout.turn_summary_path(1).read_text(encoding="utf-8") == ""
    assert layout.turn_trace_path(1).is_file()


async def test_strict_eval_rejects_multi_turn_session(tmp_path):
    output_dir = tmp_path / "strict"
    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(
            _env_file=None,
            strict_eval_mode=True,
            search_engine_only=True,
            output_dir=output_dir,
        ),
    )
    agent._session_run_id = "existing-strict-run"

    with pytest.raises(ValueError, match="cannot combine multiple session turns"):
        await agent.run("follow up", reset_history=False)

    assert not output_dir.exists()


def test_is_blank_screenshot_detects_plain_white_image():
    assert _is_blank_screenshot(Image.new("RGB", (20, 20), "white")) is True

    non_blank = Image.new("RGB", (20, 20), "white")
    non_blank.putpixel((10, 10), (0, 0, 0))
    assert _is_blank_screenshot(non_blank) is False


# ── final-output organization: result summary + attachments ────────────────


def test_as_image_path_accepts_image_under_output_root(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    (artifacts / "pdf" / "images").mkdir(parents=True)
    img = artifacts / "pdf" / "images" / "fig.jpg"
    Image.new("RGB", (4, 4), "blue").save(img)

    assert _as_image_path(str(img), artifacts) == img.resolve()
    # Relative paths resolve against the artifacts dir.
    assert _as_image_path("pdf/images/fig.jpg", artifacts) == img.resolve()


def test_as_image_path_rejects_traversal_and_non_images(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    note = artifacts / "note.txt"
    note.write_text("not an image", encoding="utf-8")

    assert _as_image_path(str(note), artifacts) is None  # wrong suffix
    assert _as_image_path("/etc/passwd", artifacts) is None  # escapes output root
    assert _as_image_path("../../secret.png", artifacts) is None  # traversal
    assert _as_image_path(None, artifacts) is None


def test_select_figure_prefers_attachment_over_last_seen(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    attached = artifacts / "attached.png"
    last = artifacts / "last.jpg"
    Image.new("RGB", (4, 4), "red").save(attached)
    Image.new("RGB", (4, 4), "green").save(last)

    chosen = _select_figure([str(attached)], str(last), artifacts)
    assert chosen == attached.resolve()
    # Falls back to the last-seen figure when no usable attachment is given.
    assert _select_figure([], str(last), artifacts) == last.resolve()
    assert _select_figure(None, None, artifacts) is None


async def test_run_persists_output_txt_and_found_figure(tmp_path, monkeypatch):
    output_dir = tmp_path / "outputs"
    sleep = AsyncMock()
    monkeypatch.setattr("webagent.agent.loop.asyncio.sleep", sleep)
    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(
            _env_file=None,
            use_vllm=False,
            max_steps=3,
            captcha_pause=False,
            post_action_wait_ms=125,
        ),
        output_dir=output_dir,
    )

    async def observe() -> BrowserState:
        return BrowserState(
            screenshot=Image.new("RGB", (32, 24), "white"),
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="now",
        )

    steps = iter(
        [
            ToolCall(tool_name="analyze_image", parameters={"path": "pdf/images/fig.jpg"}),
            ToolCall(tool_name="done", parameters={"summary": "The figure shows a rising curve."}),
        ]
    )

    async def think(browser_state: BrowserState, step_number: int = 0) -> ToolCall:
        return next(steps)

    async def act(tool_call: ToolCall) -> ToolResult:
        if tool_call.tool_name == "analyze_image":
            # Simulate a figure extracted into the pdf/ images subdir.
            fig = output_dir / "artifacts" / "pdf" / "images" / "fig.jpg"
            fig.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "blue").save(fig)
            return ToolResult(
                success=True,
                tool_name="analyze_image",
                data={"path": str(fig), "analysis": "a rising curve"},
            )
        return ToolResult(
            success=True,
            tool_name="done",
            data={"summary": "The figure shows a rising curve.", "attachments": []},
        )

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    result = await agent.run("analyze the figure")

    assert result.status == "completed"
    sleep.assert_awaited_once_with(0.125)
    layout = RunLayout.from_root(output_dir)
    output_txt = layout.summary_path
    assert output_txt.exists()
    assert output_txt.read_text(encoding="utf-8") == "The figure shows a rising curve."
    trace = json.loads(layout.trace_path.read_text(encoding="utf-8"))
    assert trace["status"] == "completed"
    assert [step["tool"] for step in trace["steps"]] == ["analyze_image", "done"]
    assert all(step["tool_duration_seconds"] is not None for step in trace["steps"])
    figure = layout.attachments_dir / "figure.jpg"
    assert figure.exists()
    assert is_blank_image(Image.open(figure)) is False


async def test_follow_up_reuses_run_and_persists_immutable_turn_snapshots(tmp_path):
    output_dir = tmp_path / "session-run"
    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(
            _env_file=None,
            use_vllm=False,
            max_steps=2,
            captcha_pause=False,
            post_action_wait_ms=0,
        ),
        output_dir=output_dir,
    )

    async def observe() -> BrowserState:
        return BrowserState(
            screenshot=Image.new("RGB", (32, 24), "white"),
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="now",
        )

    planned_steps: list[int] = []
    calls = iter(
        [
            ToolCall(tool_name="analyze_image", parameters={"path": "figures/first.png"}),
            ToolCall(tool_name="done", parameters={"summary": "first answer"}),
            ToolCall(tool_name="search", parameters={"query": "follow up"}),
            ToolCall(tool_name="done", parameters={"summary": "second answer"}),
        ]
    )

    async def think(browser_state: BrowserState, step_number: int = 0) -> ToolCall:
        planned_steps.append(step_number)
        return next(calls)

    async def act(tool_call: ToolCall) -> ToolResult:
        if tool_call.tool_name == "analyze_image":
            figure = output_dir / "artifacts" / "figures" / "first.png"
            figure.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "blue").save(figure)
            return ToolResult(
                success=True,
                tool_name="analyze_image",
                data={"path": str(figure), "analysis": "first figure"},
            )
        if tool_call.tool_name == "search":
            return ToolResult(
                success=True,
                tool_name="search",
                data={"query": "follow up", "results": [{"title": "kept"}]},
            )
        summary = "first answer" if len(planned_steps) == 2 else "second answer"
        return ToolResult(
            success=True,
            tool_name="done",
            data={"summary": summary, "attachments": []},
        )

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    first = await agent.run("first task", max_steps=2)
    layout = RunLayout.from_root(output_dir)
    first_manifest = layout.manifest_path.read_bytes()
    first_trace_snapshot = layout.turn_trace_path(1).read_bytes()
    shared_artifact = layout.files_dir / "shared.txt"
    shared_artifact.write_text("retain across turns", encoding="utf-8")

    canonical_figure = layout.attachments_dir / "figure.png"
    assert first.final_result["attachments"] == [str(canonical_figure)]
    assert canonical_figure.is_file()

    second = await agent.run("follow-up task", max_steps=2, reset_history=False)

    assert planned_steps == [1, 2, 3, 4]
    assert second.steps_taken == 4
    assert [step.step_number for step in second.history] == [1, 2, 3, 4]
    assert shared_artifact.read_text(encoding="utf-8") == "retain across turns"
    assert layout.manifest_path.read_bytes() == first_manifest
    assert layout.turn_trace_path(1).read_bytes() == first_trace_snapshot
    assert all(
        (layout.screenshots_dir / f"step_{index:03d}.jpg").is_file() for index in range(1, 5)
    )

    first_trace = json.loads(layout.turn_trace_path(1).read_text(encoding="utf-8"))
    second_trace = json.loads(layout.turn_trace_path(2).read_text(encoding="utf-8"))
    latest_trace = json.loads(layout.trace_path.read_text(encoding="utf-8"))
    assert first_trace["run_id"] == second_trace["run_id"] == latest_trace["run_id"]
    assert [step["step_number"] for step in first_trace["steps"]] == [1, 2]
    assert [step["step_number"] for step in second_trace["steps"]] == [3, 4]
    assert [step["step_number"] for step in latest_trace["steps"]] == [3, 4]
    assert latest_trace["task"] == "follow-up task"
    assert first_trace["final_result"]["attachments"] == [
        str((layout.turn_attachments_dir(1) / "figure.png").resolve())
    ]

    assert layout.turn_summary_path(1).read_text(encoding="utf-8") == "first answer"
    assert layout.turn_summary_path(2).read_text(encoding="utf-8") == "second answer"
    assert (layout.turn_attachments_dir(1) / "figure.png").is_file()
    assert list(layout.turn_attachments_dir(2).iterdir()) == []
    assert layout.summary_path.read_text(encoding="utf-8") == "second answer"
    assert list(layout.attachments_dir.iterdir()) == []


async def test_run_tracks_figure_from_pdf_analyze_figure_image_path(tmp_path):
    # pdf_analyze_figure resolves the numbered figure and returns it under
    # 'image_path'; the run must still persist it as the found figure.
    output_dir = tmp_path / "outputs"
    agent = WebAgent(
        planner=_DummyPlanner(),
        browser=_DummyBrowser(),
        tool_executor=_DummyExecutor(),
        config=AgentConfig(_env_file=None, use_vllm=False, max_steps=3, captcha_pause=False),
        output_dir=output_dir,
    )

    async def observe() -> BrowserState:
        return BrowserState(
            screenshot=Image.new("RGB", (32, 24), "white"),
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="now",
        )

    steps = iter(
        [
            ToolCall(
                tool_name="pdf_analyze_figure",
                parameters={"path": "p.pdf", "figure_number_or_caption": "1"},
            ),
            ToolCall(tool_name="done", parameters={"summary": "Figure 1 is the architecture."}),
        ]
    )

    async def think(browser_state: BrowserState, step_number: int = 0) -> ToolCall:
        return next(steps)

    async def act(tool_call: ToolCall) -> ToolResult:
        if tool_call.tool_name == "pdf_analyze_figure":
            fig = output_dir / "artifacts" / "pdf" / "images" / "fig1.png"
            fig.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "green").save(fig)
            return ToolResult(
                success=True,
                tool_name="pdf_analyze_figure",
                data={"found": True, "figure_number": "1", "image_path": str(fig)},
            )
        return ToolResult(
            success=True, tool_name="done", data={"summary": "Figure 1 is the architecture."}
        )

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    result = await agent.run("interpret figure 1")

    assert result.status == "completed"
    assert (RunLayout.from_root(output_dir).attachments_dir / "figure.png").exists()
