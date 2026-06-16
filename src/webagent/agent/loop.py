"""Main agent loop: observe → think → act → record."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from webagent.agent.history import SessionHistory
from webagent.agent.loop_detector import LoopDetector
from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot
from webagent.core.config import AgentConfig
from webagent.core.models import (
    AgentResult,
    AgentStep,
    BrowserState,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from webagent.core.protocols import AgentHook, Planner
from webagent.tools.executor import ToolExecutor
from webagent.utils.images import is_blank_image

logger = logging.getLogger("webagent")


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
        self.config = config or AgentConfig()
        # Use explicit output_dir if provided, otherwise fall back to config
        self.output_dir = Path(output_dir) if output_dir else self.config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "screenshots").mkdir(exist_ok=True)

        self._history = SessionHistory(context_length=self.config.history_context_length)
        self._hooks: list[AgentHook] = []
        self._current_task = ""
        self._task_status = TaskStatus.PENDING

        # Loop detection
        self.loop_detector: LoopDetector | None = None
        if self.config.enable_loop_detection:
            self.loop_detector = LoopDetector(
                window_size=self.config.loop_window_size,
                threshold=self.config.loop_threshold,
            )

        # Captcha handling state
        self._captcha_pause = self.config.captcha_pause
        self._captcha_timeout = self.config.captcha_timeout

    # -- Lifecycle --------------------------------------------------------

    def add_hook(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def _prepare_run_output_dir(self) -> None:
        output_dir = self.output_dir.resolve()
        if output_dir == output_dir.parent or output_dir == Path.cwd().resolve():
            raise ValueError(f"Refusing to clear unsafe output directory: {output_dir}")

        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "artifacts").mkdir(exist_ok=True)
        screenshots_dir = output_dir / "screenshots"
        screenshots_dir.mkdir(exist_ok=True)
        self.output_dir = output_dir

    async def run(
        self,
        task: str,
        max_steps: int | None = None,
        reset_history: bool = True,
    ) -> AgentResult:
        if max_steps is None:
            max_steps = self.config.max_steps
        if reset_history:
            self._history.clear()
            # The detector persists recent_actions/url_history across runs; reset
            # it so a second task on the same instance can't fire a false loop nudge.
            if self.loop_detector is not None:
                self.loop_detector.reset()

        self._prepare_run_output_dir()

        self._current_task = task
        self._task_status = TaskStatus.RUNNING

        for hook in self._hooks:
            await hook.on_task_start(task)

        start_time = time.time()
        final_result: dict[str, Any] = {}
        consecutive_failures = 0
        step_count = 0
        last_figure_path: str | None = None

        try:
            for step in range(max_steps):
                step_count = step + 1
                step_start = time.time()

                elapsed = time.time() - start_time
                if elapsed > self.config.task_timeout:
                    self._task_status = TaskStatus.TIMEOUT
                    break

                browser_state = await self._observe()

                # Check for captcha before planning
                if self._captcha_pause:
                    captcha_info = await self._check_for_captcha()
                    if captcha_info.get("detected"):
                        logger.warning(
                            "Captcha detected: %s (confidence: %.1f) - %s",
                            captcha_info["type"],
                            captcha_info["confidence"],
                            captcha_info["reason"],
                        )
                        # Add captcha marker to history for visibility
                        # Agent will continue but user is notified
                        # In production, this could pause and wait for manual solving

                tool_call = await self._think(browser_state)
                if tool_call is None:
                    screenshot_path = self.output_dir / "screenshots" / f"step_{step_count:03d}.jpg"
                    _save_step_screenshot(browser_state, screenshot_path)
                    consecutive_failures += 1
                    if consecutive_failures >= self.config.max_consecutive_failures:
                        self._task_status = TaskStatus.FAILED
                        break
                    continue

                tool_result = await self._act(tool_call)

                screenshot_path = self.output_dir / "screenshots" / f"step_{step_count:03d}.jpg"
                post_action_state = await self._observe()
                _save_step_screenshot(post_action_state, screenshot_path)

                # Check timeout after tool execution (catches long-running tools)
                elapsed = time.time() - start_time
                if elapsed > self.config.task_timeout:
                    self._task_status = TaskStatus.TIMEOUT
                    break

                if not tool_result.success:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                    # Remember the most recent image the agent worked with so it
                    # can be persisted as the "found figure" on completion.
                    if tool_call.tool_name in _FIGURE_TOOLS:
                        candidate = tool_result.data.get("path") or tool_result.data.get(
                            "image_path"
                        )
                        if isinstance(candidate, str):
                            last_figure_path = candidate

                step_duration = time.time() - step_start
                agent_step = AgentStep(
                    step_number=step_count,
                    timestamp=datetime.now().isoformat(),
                    browser_state=browser_state,
                    tool_call=tool_call,
                    tool_result=tool_result,
                    duration_seconds=step_duration,
                )
                self._history.add(agent_step)

                for hook in self._hooks:
                    await hook.on_step_complete(step_count, tool_call, tool_result)

                if tool_call.tool_name == "done":
                    self._task_status = TaskStatus.COMPLETED
                    final_result = tool_result.data
                    figure = _select_figure(
                        final_result.get("attachments"),
                        last_figure_path,
                        self.output_dir / "artifacts",
                    )
                    _persist_final_outputs(self.output_dir, final_result.get("summary", ""), figure)
                    break

                if consecutive_failures >= self.config.max_consecutive_failures:
                    self._task_status = TaskStatus.FAILED
                    break

                await asyncio.sleep(0.5)
            else:
                self._task_status = TaskStatus.MAX_STEPS_REACHED

        except KeyboardInterrupt:
            self._task_status = TaskStatus.INTERRUPTED
        except Exception as exc:
            # Treat browser disconnection as a failure rather than a hard crash
            err_msg = str(exc).lower()
            if any(
                k in err_msg
                for k in (
                    "target closed",
                    "browser has been closed",
                    "connection closed",
                    "target page",
                )
            ):
                logger.error("Browser disconnected unexpectedly: %s", exc)
                self._task_status = TaskStatus.FAILED
            else:
                self._task_status = TaskStatus.FAILED
                raise
        finally:
            total_duration = time.time() - start_time
            # Always notify hooks of task end — even when an exception propagates —
            # so run()'s contract holds regardless of how the loop exits.
            for hook in self._hooks:
                await hook.on_task_end(self._task_status.value, step_count)

        return AgentResult(
            success=self._task_status == TaskStatus.COMPLETED,
            status=self._task_status.value,
            steps_taken=step_count,
            total_duration=total_duration,
            final_result=final_result,
            history=self._history.steps,
        )

    # -- Internal ---------------------------------------------------------

    async def _observe(self) -> BrowserState:
        for attempt in range(3):
            try:
                try:
                    await self._browser.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass

                use_cdp = self.config.use_cdp
                max_elements = self.config.max_snapshot_elements

                snapshot = await take_snapshot(
                    self._browser.page,
                    task=self._current_task,
                    use_cdp=use_cdp,
                    max_elements=max_elements,
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
                    timestamp=datetime.now().isoformat(),
                )
            except Exception as e:
                logger.warning("Observe attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)
        return BrowserState(
            dom_summary="(page loading)",
            url=self._browser.page.url,
            title="",
            timestamp=datetime.now().isoformat(),
        )

    async def _think(self, browser_state: BrowserState) -> ToolCall | None:
        tool_descriptions = self._tool_executor.get_tool_descriptions()
        history_text = self._history.format_for_llm()

        # Check for loops before planning — inject nudge into history so LLM sees it
        if self.loop_detector:
            is_looping, nudge = self.loop_detector.is_looping()
            if is_looping:
                logger.warning("Loop detected: %s", nudge)
                history_text = f"{history_text}\n\n⚠️ LOOP DETECTED: {nudge}"

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

        try:
            tool_call = await self._planner.plan_action(
                task=self._current_task,
                browser_state=browser_state,
                history_text=history_text,
                available_tools=tool_descriptions,
            )

            # Record action in loop detector (include page content hash for stagnation detection)
            if tool_call and self.loop_detector:
                page_hash = hashlib.md5(
                    browser_state.dom_summary.encode("utf-8", errors="replace")
                ).hexdigest()
                self.loop_detector.add_action(
                    tool_name=tool_call.tool_name,
                    page_url=browser_state.url,
                    page_hash=page_hash,
                    parameters=tool_call.parameters,
                )

            return tool_call
        except Exception as e:
            logger.error("Planner error: %s (%s)", e or repr(e), type(e).__name__)
            return None

    async def _check_for_captcha(self) -> dict:
        """Check if current page has a captcha challenge.

        Returns:
            Dictionary with captcha detection results.
        """
        return await self._browser.check_captcha()

    async def _act(self, tool_call: ToolCall) -> ToolResult:
        return await self._tool_executor.execute(tool_call)


_is_blank_screenshot = is_blank_image


def _save_step_screenshot(
    browser_state: BrowserState,
    screenshot_path: Path,
) -> None:
    if browser_state.screenshot is not None:
        browser_state.screenshot.save(screenshot_path)


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


def _persist_final_outputs(output_dir: Path, summary: str, figure: Path | None) -> None:
    """Write the final LLM analysis to output.txt and copy the found figure.

    Best-effort: failures are logged, never raised, so persistence cannot crash a
    task that has otherwise completed successfully.
    """
    artifacts_dir = output_dir / "artifacts"
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "output.txt").write_text(summary or "", encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write output.txt: %s", exc)

    if figure is None:
        return
    dest = artifacts_dir / f"figure{figure.suffix.lower()}"
    try:
        if figure.resolve() != dest.resolve():
            shutil.copyfile(figure, dest)
        logger.info("Saved found figure to %s", dest)
    except OSError as exc:
        logger.warning("Failed to save figure %s: %s", figure, exc)
