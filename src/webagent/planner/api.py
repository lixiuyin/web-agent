"""Remote API planner (OpenAI-compatible endpoints)."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from webagent.core.models import BrowserState, ToolCall
from webagent.planner.base import (
    STRUCTURED_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_prompt,
    parse_llm_response,
)
from webagent.planner.observation_input import input_metadata
from webagent.planner.provider_response import (
    _required_tool_choice_unsupported,
    _strip_thinking_tags,
    _structured_output_unsupported,
)
from webagent.planner.structured import (
    JSON_SCHEMA_SYSTEM_PROMPT,
    NATIVE_TOOL_SYSTEM_PROMPT,
    PlannerOutputMode,
    normalize_output_mode,
    openai_function_tools,
    openai_response_format,
    parse_provider_tool_call,
    response_text,
)
from webagent.planner.vision import VisionSupport
from webagent.tools.registry import ToolSpec

logger = logging.getLogger("webagent")

_LOCAL_ARTIFACT_TOOLS = (
    "download_pdf(",
    "pdf_parse(",
    "pdf_analyze_figure(",
    "analyze_image(",
    "read_image(",
)


def _local_artifact_history(history_text: str, url: str) -> bool:
    """Whether a local preview is redundant with structured artifact evidence."""
    return url.casefold().startswith("file://") and any(
        marker in history_text for marker in _LOCAL_ARTIFACT_TOOLS
    )


def _planning_screenshot_needed(browser_state: BrowserState, history_text: str) -> bool:
    """Use visual tokens only when DOM text is unlikely to ground the next action."""
    if browser_state.requires_visual or "visual-grounding" in history_text.casefold():
        return True
    dom_chars = browser_state.observation_metadata.get(
        "dom_content_chars", len(browser_state.dom_summary.strip())
    )
    if isinstance(dom_chars, int) and dom_chars < 400:
        return True
    path = urlparse(browser_state.url).path.casefold()
    return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


class APIPlanner:
    """Calls a remote OpenAI-compatible chat API.

    Automatically detects whether the model supports vision (image) input
    during ``load()``.  If not, screenshots are omitted from prompts.

    For providers like MiniMax that expose vision through a **separate** VLM
    endpoint (not the chat completions API), the planner auto-detects the VLM
    URL and routes ``analyze_image`` calls there.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model_name: str = "glm-4.7",
        timeout: int = 120,
        temperature: float = 0.7,
        use_structured_output: bool = False,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        vision_max_tokens: int = 8192,
        vision_brief_max_tokens: int = 1200,
        vision_max_words: int = 350,
        hard_timeout: int = 300,
        transient_retries: int = 2,
        retry_base_seconds: float = 0.5,
        retry_max_seconds: float = 10.0,
        output_mode: str | None = None,
        screenshot_mode: str = "auto",
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout
        # Hard wall-clock cap per request. httpx's read timeout resets on every
        # received byte, so a server that trickles bytes (reasoning models often
        # do) can keep a connection alive far past ``timeout``. asyncio.wait_for
        # enforces a true upper bound so one stalled call cannot eat the whole
        # task budget; on expiry the call fails and the agent recovers.
        self.hard_timeout = max(hard_timeout, timeout)
        self.transient_retries = max(0, transient_retries)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self.retry_max_seconds = max(0.0, retry_max_seconds)
        self._last_transport_retries = 0
        self.temperature = temperature
        self.use_structured_output = use_structured_output
        configured_mode = output_mode or ("auto" if use_structured_output else "prompt-json")
        self.output_mode: PlannerOutputMode = normalize_output_mode(configured_mode)
        self._effective_output_mode: PlannerOutputMode | None = (
            None if self.output_mode == "auto" else self.output_mode
        )
        self._tool_specs: list[ToolSpec] = []
        self._structured_fallbacks: list[dict[str, Any]] = []
        self._call_structured_fallbacks: list[dict[str, Any]] = []
        self._native_tool_choice = "required"
        # Cap output length. Generous enough for reasoning models (which spend
        # tokens thinking before emitting the action JSON) without truncation.
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        if screenshot_mode not in {"auto", "always", "never"}:
            raise ValueError("screenshot_mode must be one of: auto, always, never")
        self.screenshot_mode = screenshot_mode
        self.vision_max_tokens = vision_max_tokens
        self.vision_brief_max_tokens = vision_brief_max_tokens
        self.vision_max_words = vision_max_words
        self._vision = VisionSupport(self)
        self._last_call_metadata: dict[str, Any] = {}
        self._last_planning_input_metadata: dict[str, Any] = {}
        self._planning_request_active = False

    @property
    def last_planning_input_metadata(self) -> dict[str, Any]:
        return dict(self._last_planning_input_metadata)

    @property
    def last_call_metadata(self) -> dict[str, Any]:
        """Metadata for the latest planning call, excluding response content."""
        return dict(self._last_call_metadata)

    @property
    def effective_output_mode(self) -> PlannerOutputMode:
        """Output mode currently selected after any capability fallback."""
        return self._effective_output_mode or self.output_mode

    @property
    def structured_fallbacks(self) -> list[dict[str, Any]]:
        """Capability downgrades performed in auto mode during this session."""
        return [dict(item) for item in self._structured_fallbacks]

    def configure_tools(self, specs: Sequence[ToolSpec]) -> None:
        """Install the policy-filtered tool catalog used in provider requests."""
        unique: dict[str, ToolSpec] = {}
        for spec in specs:
            if not spec.name or spec.name in unique:
                raise ValueError(f"Duplicate or empty planner tool name: {spec.name!r}")
            unique[spec.name] = spec
        self._tool_specs = list(unique.values())

    async def load(self) -> None:
        """Probe available vision routes for this session."""
        await self._vision.load()

    async def unload(self) -> None:
        pass

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall | None:
        self._last_planning_input_metadata = {}
        self._last_call_metadata = {}
        provider_mode = self._initial_planning_mode()
        response_instruction = (
            "SELECT EXACTLY ONE ACTION USING THE REQUIRED PROVIDER FORMAT:"
            if provider_mode != "prompt-json"
            else "YOUR RESPONSE (JSON ONLY):"
        )
        prompt, screenshot_b64 = build_prompt(
            task,
            browser_state,
            history_text,
            available_tools,
            response_instruction=response_instruction,
        )

        omission_reason = self._screenshot_omission_reason(
            browser_state, history_text, screenshot_b64
        )
        if omission_reason is not None:
            screenshot_b64 = None
        self._last_planning_input_metadata = input_metadata(
            browser_state, screenshot_b64, omission_reason
        )
        logger.info(
            "Planner request context: dom_chars=%d screenshot_captured=%s screenshot_included=%s",
            len(browser_state.dom_summary),
            browser_state.screenshot is not None,
            screenshot_b64 is not None,
        )
        self._call_structured_fallbacks = []
        self._planning_request_active = True
        try:
            if provider_mode == "prompt-json":
                raw = await self._call(prompt, screenshot_b64)
                self._annotate_output_mode("prompt-json")
                return parse_llm_response(raw)
            return await self._call_structured(prompt, screenshot_b64, provider_mode)
        finally:
            self._planning_request_active = False

    def _screenshot_omission_reason(
        self, state: BrowserState, history: str, screenshot_b64: str | None
    ) -> str | None:
        if state.screenshot is None:
            return "not_captured"
        if screenshot_b64 is None:
            return "blank_image"
        if not self._vision.supports_images:
            return "vision_unsupported"
        if self.screenshot_mode == "never":
            return "mode_never"
        if self.screenshot_mode == "auto" and not _planning_screenshot_needed(state, history):
            return "auto_dom_sufficient"
        if _local_artifact_history(history, state.url):
            return "local_artifact_evidence"
        return None

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        """Analyze an image using the available VLM or chat vision route."""
        return await self._vision.analyze_image(image, question)

    async def estimate_task_success(
        self,
        *,
        task: str,
        status: str,
        history_text: str,
    ) -> float:
        """Self-report terminal success likelihood before an external judge is consulted."""
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Estimate whether the task is actually complete and correct from the "
                        "recorded execution only. Return a calibrated probability, not optimism."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"TASK:\n{task}\n\nTERMINAL STATUS: {status}\n\n"
                        f"EXECUTION HISTORY:\n{history_text[-12000:]}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 80,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "task_success_confidence",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "success_probability": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            }
                        },
                        "required": ["success_probability"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        try:
            data = await self._post_data(payload)
        except httpx.HTTPStatusError as exc:
            if not _structured_output_unsupported(exc, "json-schema"):
                raise
            payload.pop("response_format", None)
            messages = payload["messages"]
            assert isinstance(messages, list) and isinstance(messages[-1], dict)
            messages[-1]["content"] = (
                str(messages[-1]["content"])
                + '\n\nReturn JSON only: {"success_probability": number from 0 to 1}.'
            )
            data = await self._post_data(payload)
        raw = _strip_thinking_tags(response_text(data))
        match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
        decoded = json.loads(match.group(0) if match is not None else raw)
        probability = decoded.get("success_probability") if isinstance(decoded, dict) else None
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
            or not 0.0 <= float(probability) <= 1.0
        ):
            raise ValueError("provider returned an invalid task-success probability")
        return float(probability)

    @property
    def vision_actually_works(self) -> bool:
        """Whether at least one vision route is available."""
        return self._vision.vision_actually_works

    # -- internals --------------------------------------------------------

    def _initial_planning_mode(self) -> PlannerOutputMode:
        """Select a mode without claiming native support before a successful call."""
        if not self._tool_specs:
            if self.output_mode in {"native-tools", "json-schema"}:
                raise RuntimeError(
                    f"planner output mode {self.output_mode!r} requires configure_tools(specs)"
                )
            return "prompt-json"
        if self._effective_output_mode is not None:
            return self._effective_output_mode
        return "native-tools"

    async def _call_structured(
        self,
        prompt: str,
        screenshot_b64: str | None,
        initial_mode: PlannerOutputMode,
    ) -> ToolCall | None:
        modes = self._structured_mode_ladder(initial_mode)
        for index, mode in enumerate(modes):
            if mode == "prompt-json":
                raw = await self._call(prompt, screenshot_b64)
                if self.output_mode == "auto":
                    self._effective_output_mode = mode
                self._annotate_output_mode(mode)
                return parse_llm_response(raw)

            try:
                data = await self._post_structured_with_choice_fallback(
                    prompt, screenshot_b64, mode
                )
            except httpx.HTTPStatusError as exc:
                if self.output_mode != "auto" or not _structured_output_unsupported(exc, mode):
                    raise
                next_mode = modes[index + 1] if index + 1 < len(modes) else None
                if next_mode is None:
                    raise
                self._record_structured_fallback(mode, next_mode, exc)
                continue

            raw = _strip_thinking_tags(response_text(data))
            call = (
                parse_provider_tool_call(data)
                if mode == "native-tools"
                else parse_llm_response(raw)
            )
            self._capture_response_metadata(data, len(raw))
            if self.output_mode == "auto":
                self._effective_output_mode = mode
            self._annotate_output_mode(mode)
            if call is not None and call.tool_name not in {spec.name for spec in self._tool_specs}:
                logger.warning("Provider returned unexposed tool call: %s", call.tool_name)
                return None
            return call
        return None

    async def _post_structured_with_choice_fallback(
        self, prompt: str, screenshot_b64: str | None, mode: PlannerOutputMode
    ) -> dict[str, Any]:
        payload = self._structured_payload(prompt, screenshot_b64, mode)
        try:
            return await self._post_data(payload)
        except httpx.HTTPStatusError as exc:
            if not (
                self.output_mode == "auto"
                and mode == "native-tools"
                and self._native_tool_choice == "required"
                and _required_tool_choice_unsupported(exc)
            ):
                raise
            self._record_structured_fallback("native-tools:required", "native-tools:auto", exc)
            self._native_tool_choice = "auto"
        return await self._post_data(self._structured_payload(prompt, screenshot_b64, mode))

    def _structured_mode_ladder(
        self, initial_mode: PlannerOutputMode
    ) -> tuple[PlannerOutputMode, ...]:
        if self.output_mode != "auto":
            return (initial_mode,)
        ladder: tuple[PlannerOutputMode, ...] = (
            "native-tools",
            "json-schema",
            "prompt-json",
        )
        try:
            return ladder[ladder.index(initial_mode) :]
        except ValueError:
            return ("prompt-json",)

    def _structured_payload(
        self,
        prompt: str,
        screenshot_b64: str | None,
        mode: PlannerOutputMode,
    ) -> dict[str, Any]:
        payload = self._base_chat_payload(
            prompt,
            screenshot_b64,
            system_prompt=(
                NATIVE_TOOL_SYSTEM_PROMPT if mode == "native-tools" else JSON_SCHEMA_SYSTEM_PROMPT
            ),
        )
        if mode == "native-tools":
            payload["tools"] = openai_function_tools(self._tool_specs)
            payload["tool_choice"] = self._native_tool_choice
            payload["parallel_tool_calls"] = False
        elif mode == "json-schema":
            payload["response_format"] = openai_response_format(self._tool_specs)
        else:
            raise ValueError(f"Structured payload requested for mode {mode!r}")
        return payload

    def _record_structured_fallback(
        self,
        source: str,
        target: str,
        exc: httpx.HTTPStatusError,
    ) -> None:
        response = exc.response
        event = {
            "from": source,
            "to": target,
            "status_code": response.status_code,
            "reason": response.text[:300],
        }
        self._structured_fallbacks.append(event)
        self._call_structured_fallbacks.append(event)
        logger.warning(
            "Planner provider does not support %s; falling back to %s (%d)",
            source,
            target,
            response.status_code,
        )

    def _annotate_output_mode(self, effective: PlannerOutputMode) -> None:
        self._last_call_metadata.update(
            {
                "requested_output_mode": self.output_mode,
                "effective_output_mode": effective,
                "structured_fallbacks": [dict(item) for item in self._call_structured_fallbacks],
                "session_structured_fallback_count": len(self._structured_fallbacks),
                "native_tool_choice": self._native_tool_choice,
            }
        )

    async def _call(self, prompt: str, screenshot_b64: str | None) -> str:
        # Choose system prompt based on configuration
        system_prompt = STRUCTURED_SYSTEM_PROMPT if self.use_structured_output else SYSTEM_PROMPT
        payload = self._base_chat_payload(prompt, screenshot_b64, system_prompt=system_prompt)
        return await self._post(payload)

    def _base_chat_payload(
        self,
        prompt: str,
        screenshot_b64: str | None,
        *,
        system_prompt: str,
    ) -> dict[str, Any]:
        if screenshot_b64:
            user_content: str | list[dict[str, Any]] = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                },
            ]
        else:
            user_content = prompt
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {
                "effort": self.reasoning_effort,
                "exclude": True,
            }
        return payload

    async def _bounded_post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: int | None = None,
    ) -> httpx.Response:
        """POST with a per-read timeout AND a hard wall-clock cap.

        httpx's read timeout resets on every received byte, so it cannot bound a
        server that trickles data. ``asyncio.wait_for`` enforces a true upper
        bound (``hard_timeout``); on expiry it raises ``TimeoutError``, which
        callers let propagate so the agent records a failed step and recovers.
        A caller may pass ``timeout`` to extend the read timeout (e.g. slow
        reasoning-model vision calls); the wall-clock cap stays ``hard_timeout``.
        """
        read_timeout = timeout if timeout is not None else self.timeout
        async with httpx.AsyncClient(timeout=read_timeout) as client:
            return await asyncio.wait_for(
                client.post(url, headers=headers, json=payload),
                timeout=self.hard_timeout,
            )

    async def _post(self, payload: dict[str, Any], timeout: int | None = None) -> str:
        data = await self._post_data(payload, timeout=timeout)
        response = _strip_thinking_tags(response_text(data))
        self._capture_response_metadata(data, len(response))
        logger.debug("API response length: %d chars", len(response))
        return response

    async def _post_data(
        self, payload: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        """Return the raw provider object needed for native tool-call parsing."""
        if self._planning_request_active:
            self._last_planning_input_metadata.update(
                request_dispatched=True,
                screenshot_sent=self._last_planning_input_metadata["screenshot_included"],
            )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        resp: httpx.Response | None = None
        self._last_transport_retries = 0
        for attempt in range(self.transient_retries + 1):
            resp = await self._bounded_post(self.api_url, payload, headers, timeout=timeout)
            transient = resp.status_code == 429 or 500 <= resp.status_code < 600
            if not transient or attempt >= self.transient_retries:
                break
            retry_after = _retry_after_seconds(resp)
            delay = min(
                self.retry_max_seconds,
                retry_after if retry_after is not None else self.retry_base_seconds * (2**attempt),
            )
            self._last_transport_retries += 1
            logger.warning(
                "Planner API %d; retrying in %.2fs (%d/%d)",
                resp.status_code,
                delay,
                attempt + 1,
                self.transient_retries,
            )
            await asyncio.sleep(delay)
        assert resp is not None
        # Preserve exhausted transport retries even when ``raise_for_status``
        # prevents normal response metadata capture.
        self._last_call_metadata["transport_retries"] = self._last_transport_retries
        if resp.status_code != 200:
            logger.error("API %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Planner API response must be a JSON object")

        # Log response structure for debugging
        logger.debug("API response keys: %s", list(data.keys()))
        return data

    def _capture_response_metadata(self, data: dict[str, Any], response_length: int) -> None:
        choices = data.get("choices")
        finish_reason = (
            choices[0].get("finish_reason")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        self._capture_call_metadata(data, finish_reason, response_length)

    def _capture_call_metadata(
        self, data: dict[str, Any], finish_reason: Any, response_length: int
    ) -> None:
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        self._last_call_metadata = {
            "response_length": response_length,
            "finish_reason": str(finish_reason) if finish_reason is not None else None,
            "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
            "completion_tokens": _optional_int(usage.get("completion_tokens")),
            "total_tokens": _optional_int(usage.get("total_tokens")),
            "transport_retries": self._last_transport_retries,
        }


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)
