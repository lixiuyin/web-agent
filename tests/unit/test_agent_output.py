"""Tests for agent output directory handling."""

from __future__ import annotations

from PIL import Image

from webagent.agent.loop import (
    WebAgent,
    _as_image_path,
    _is_blank_screenshot,
    _select_figure,
)
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall, ToolResult
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


async def test_agent_run_clears_existing_output_dir_contents(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    sentinel = output_dir / "keep.txt"
    sentinel.write_text("delete before run", encoding="utf-8")
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir()
    stale_screenshot = screenshots_dir / "step_001.jpg"
    stale_screenshot.write_bytes(b"old screenshot")
    unrelated_screenshot = screenshots_dir / "manual.jpg"
    unrelated_screenshot.write_bytes(b"manual screenshot")

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

    async def think(browser_state: BrowserState) -> ToolCall:
        return ToolCall(tool_name="done", parameters={"summary": "ok"})

    async def act(tool_call: ToolCall) -> ToolResult:
        return ToolResult(success=True, tool_name="done", data={"summary": "ok"})

    agent._observe = observe  # type: ignore[method-assign]
    agent._think = think  # type: ignore[method-assign]
    agent._act = act  # type: ignore[method-assign]

    result = await agent.run("task")

    assert result.status == "completed"
    assert not sentinel.exists()
    assert not unrelated_screenshot.exists()
    assert (output_dir / "artifacts").is_dir()
    assert (output_dir / "screenshots").is_dir()
    screenshot = output_dir / "screenshots" / "step_001.jpg"
    assert screenshot.exists()
    assert screenshot.read_bytes() != b"old screenshot"
    assert is_blank_image(Image.open(screenshot)) is True


def test_is_blank_screenshot_detects_plain_white_image():
    assert _is_blank_screenshot(Image.new("RGB", (20, 20), "white")) is True

    non_blank = Image.new("RGB", (20, 20), "white")
    non_blank.putpixel((10, 10), (0, 0, 0))
    assert _is_blank_screenshot(non_blank) is False


# ── final-output organization: output.txt + figure ──────────────────────────


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


async def test_run_persists_output_txt_and_found_figure(tmp_path):
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
            ToolCall(tool_name="analyze_image", parameters={"path": "pdf/images/fig.jpg"}),
            ToolCall(tool_name="done", parameters={"summary": "The figure shows a rising curve."}),
        ]
    )

    async def think(browser_state: BrowserState) -> ToolCall:
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
    output_txt = output_dir / "artifacts" / "output.txt"
    assert output_txt.exists()
    assert output_txt.read_text(encoding="utf-8") == "The figure shows a rising curve."
    figure = output_dir / "artifacts" / "figure.jpg"
    assert figure.exists()
    assert is_blank_image(Image.open(figure)) is False


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

    async def think(browser_state: BrowserState) -> ToolCall:
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
    assert (output_dir / "artifacts" / "figure.png").exists()
