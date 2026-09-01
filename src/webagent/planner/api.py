"""Remote API planner (OpenAI-compatible endpoints)."""

from __future__ import annotations

import asyncio
import base64
import io
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
from webagent.planner._vision_heuristics import has_visual_content, indicates_no_vision
from webagent.planner.base import (
    STRUCTURED_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_prompt,
    parse_llm_response,
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
from webagent.tools.registry import ToolSpec

# Matches <think>...</think> blocks produced by reasoning models (DeepSeek, GLM-Z1, etc.)
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Matches unclosed <think> tags (model didn't emit </think>)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)

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
    if "visual-grounding" in history_text.casefold():
        return True
    if len(browser_state.dom_summary.strip()) < 400:
        return True
    path = urlparse(browser_state.url).path.casefold()
    return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> reasoning chains from model responses.

    Reasoning models (DeepSeek-R1, GLM-Z1, QwQ, MiniMax-M2.7, etc.) may inline their
    chain-of-thought inside <think>...</think> tags before the actual answer.
    Some models omit the closing </think> tag — we handle that too.
    """
    # First strip properly closed tags
    stripped = _THINK_TAG_RE.sub("", text).strip()
    # Handle unclosed <think> tags (everything from <think> to end)
    if re.search(r"<think>", stripped, re.IGNORECASE):
        stripped = _THINK_UNCLOSED_RE.sub("", stripped).strip()
    # If stripping removed everything, fall back to the original text so we
    # don't return an empty string to callers that have no other fallback.
    return stripped if stripped else text.strip()


_probe_image_cache: str | None = None


def _probe_image_b64() -> str:
    """Return a base64 JPEG of a solid red square for vision probing.

    Generated at runtime with Pillow so it is always valid base64 — a previously
    hardcoded constant was malformed and strict providers (e.g. Xiaomi via
    OpenRouter) rejected it with HTTP 400 "invalid base64 format", causing every
    vision-capable model to be mis-detected as text-only. A meaningful image
    (not 1x1) forces the model to actually demonstrate it can see.
    """
    global _probe_image_cache
    if _probe_image_cache is None:
        import base64
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (48, 48), (220, 30, 30)).save(buf, format="JPEG")
        _probe_image_cache = base64.b64encode(buf.getvalue()).decode("ascii")
    return _probe_image_cache


def _detect_vlm_url(api_url: str) -> str | None:
    """Auto-detect a separate VLM (vision) endpoint from the chat API URL.

    MiniMax exposes vision through ``/v1/coding_plan/vlm`` rather than the
    chat completions endpoint.  Returns *None* when no separate VLM endpoint
    is known for the given provider.
    """
    lower = api_url.lower()
    if "minimaxi.com" in lower or "minimax.io" in lower:
        # Derive base from chat URL, e.g.
        #   https://api.minimaxi.com/v1/chat/completions → https://api.minimaxi.com
        from urllib.parse import urlparse

        parsed = urlparse(api_url)
        return f"{parsed.scheme}://{parsed.netloc}/v1/coding_plan/vlm"
    return None


class APIPlanner:
    """Calls a remote OpenAI-compatible chat API.

    Automatically detects whether the model supports vision (image) input
    during ``load()``.  If not, screenshots are omitted from prompts.

    For providers like MiniMax that expose vision through a **separate** VLM
    endpoint (not the chat completions API), the planner auto-detects the VLM
    URL and routes ``analyze_image`` calls there.
    """

    # Retries within a single ``analyze_image`` call: flaky vision models
    # occasionally miss an image they can otherwise read, so retry before giving up.
    _VISION_RETRY_ATTEMPTS = 2
    # Consecutive fully-failed ``analyze_image`` calls before chat vision is
    # latched off for the rest of the session. Keeps one blip from disabling vision.
    _VISION_FAILURE_LIMIT = 2

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
        vision_max_tokens: int = 2000,
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
        self._supports_vision: bool | None = None  # chat API accepts images?
        self._vision_actually_works: bool = True  # chat API vision produces real results?
        self._vision_failure_count: int = 0  # consecutive analyze_image calls that saw no image
        self._vlm_url: str | None = _detect_vlm_url(api_url)  # separate VLM endpoint
        self._vlm_available: bool = False  # probed during load()
        self._last_call_metadata: dict[str, Any] = {}

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
        """Probe the API to detect vision support."""
        self._supports_vision = await self._probe_vision()
        if self._supports_vision:
            if self._vision_actually_works:
                logger.info("Model %s: vision supported (chat API)", self.model_name)
            else:
                logger.info(
                    "Model %s: vision format accepted but model cannot see images",
                    self.model_name,
                )
        else:
            self._vision_actually_works = False
            logger.info("Model %s: text-only (chat API)", self.model_name)

        # If chat API vision doesn't work, try a separate VLM endpoint.
        if not self.vision_actually_works and self._vlm_url:
            self._vlm_available = await self._probe_vlm()
            if self._vlm_available:
                logger.info(
                    "Model %s: VLM endpoint available at %s",
                    self.model_name,
                    self._vlm_url,
                )
            else:
                logger.info(
                    "Model %s: VLM endpoint probe failed (%s)",
                    self.model_name,
                    self._vlm_url,
                )

    async def unload(self) -> None:
        pass

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall | None:
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

        if (
            not self._supports_vision
            or self.screenshot_mode == "never"
            or (
                self.screenshot_mode == "auto"
                and not _planning_screenshot_needed(browser_state, history_text)
            )
        ):
            screenshot_b64 = None
        elif screenshot_b64 and _local_artifact_history(history_text, browser_state.url):
            # A browser PDF/image preview is redundant once a structured file/PDF
            # tool has returned the path and evidence. Omitting it avoids an
            # expensive second visual interpretation during action planning.
            screenshot_b64 = None
        logger.info(
            "Planner request context: dom_chars=%d screenshot_captured=%s screenshot_sent=%s",
            len(browser_state.dom_summary),
            browser_state.screenshot is not None,
            screenshot_b64 is not None,
        )
        self._last_call_metadata = {}
        self._call_structured_fallbacks = []
        if provider_mode == "prompt-json":
            raw = await self._call(prompt, screenshot_b64)
            self._annotate_output_mode("prompt-json")
            return parse_llm_response(raw)
        return await self._call_structured(prompt, screenshot_b64, provider_mode)

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        """Analyze an image using vision capabilities.

        Routes to the separate VLM endpoint when available (e.g. MiniMax),
        otherwise falls back to the chat completions API with inline images.

        Args:
            image: PIL Image to analyze
            question: Question about the image

        Returns:
            Text description of the image based on the question
        """
        # Check if ANY vision path is available
        can_use_chat_vision = self._supports_vision and self._vision_actually_works
        can_use_vlm = self._vlm_available and self._vlm_url

        if not can_use_chat_vision and not can_use_vlm:
            return (
                "Vision API is not available to analyze the image. "
                "Use 'pdf_get_figure_info' for figure captions, "
                "or 'pdf_extract_text'/'pdf_search' to read surrounding text."
            )

        # Optimize image: resize if too large (max 2048px on longest side)
        max_size = 2048
        if max(image.width, image.height) > max_size:
            ratio = max_size / max(image.width, image.height)
            new_size = (int(image.width * ratio), int(image.height * ratio))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            logger.info("analyze_image: resized to %dx%d", new_size[0], new_size[1])

        # Convert to base64
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        logger.info(
            "analyze_image: image size=%dx%d, base64_len=%d, question_len=%d",
            image.width,
            image.height,
            len(b64),
            len(question),
        )

        # Prefer VLM endpoint when available (MiniMax, etc.)
        if can_use_vlm:
            return await self._analyze_image_vlm(b64, question)

        # Fall back to chat completions API with inline image
        return await self._analyze_image_chat(b64, question)

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

    async def _analyze_image_vlm(self, b64: str, question: str) -> str:
        """Analyze an image via a dedicated VLM endpoint (e.g. MiniMax)."""
        assert self._vlm_url is not None
        payload = {
            "prompt": question,
            "image_url": f"data:image/jpeg;base64,{b64}",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        logger.debug("analyze_image_vlm: sending request to %s", self._vlm_url)
        resp = await self._bounded_post(self._vlm_url, payload, headers)
        if resp.status_code != 200:
            logger.error("VLM API %d: %s", resp.status_code, resp.text[:500])
            self._vlm_available = False
            return (
                f"VLM API returned error {resp.status_code}. "
                "Use 'pdf_extract_text' or 'pdf_search' to read text instead."
            )
        data = resp.json()

        # Check for API-level errors
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            logger.error("VLM API error: %s", base_resp.get("status_msg", ""))
            return f"VLM API error: {base_resp.get('status_msg', 'unknown')}"

        content = data.get("content", "")
        content = _strip_thinking_tags(content)
        logger.info(
            "analyze_image_vlm: response_len=%d, starts_with=%s",
            len(content),
            content[:100] if content else "",
        )
        return content if content else "VLM returned empty response."

    async def _analyze_image_chat(self, b64: str, question: str) -> str:
        """Analyze an image via the chat completions API (inline image).

        Retries within the call on a transient "cannot see image" response, and
        only latches chat vision off after ``_VISION_FAILURE_LIMIT`` consecutive
        failed calls, so a single blip does not disable vision for the session.
        """
        # Scale the directive and token budget to the question's complexity.
        # A terse question ("what color?") gets a concise answer so a reasoning
        # model answers directly; a detailed one ("describe ... in detail") gets
        # a thorough answer with more headroom so the chain-of-thought doesn't
        # crowd out the content.
        q = question.strip().lower()
        wants_detail = len(question.strip()) > 80 or any(
            k in q
            for k in (
                "in detail",
                "thorough",
                "comprehensive",
                "describe",
                "explain",
                "analyze",
                "purpose",
                "key finding",
            )
        )
        if wants_detail:
            directive = (
                "Provide a thorough, structured answer covering the purpose, key "
                "components, and findings. Omit meta-commentary and step-by-step reasoning. "
                f"Keep the answer under {self.vision_max_words} words. "
            )
            max_tokens = self.vision_max_tokens
        else:
            directive = (
                "Answer concisely, in a few short paragraphs, without showing your reasoning. "
            )
            max_tokens = min(self.vision_max_tokens, self.vision_brief_max_tokens)

        prompt_with_instruction = (
            "You are an image analysis assistant. Carefully observe the image "
            "and answer the user's question. If the content cannot be clearly "
            "seen or determined, state it honestly. "
            f"{directive}"
            f"\n\nUser question: {question}"
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_with_instruction},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "temperature": max(0.3, self.temperature),
            "max_tokens": max_tokens,
        }

        for attempt in range(1, self._VISION_RETRY_ATTEMPTS + 1):
            response = await self._post(payload, timeout=self.hard_timeout)
            response = self._clean_vision_response(response, prompt_with_instruction)
            # A blank response carries no analysis — treat it like a "cannot see
            # image" answer and retry, rather than returning a useless "" to the
            # caller (which would surface as an empty `vision_analysis`).
            if response.strip() and not indicates_no_vision(response):
                self._vision_failure_count = 0  # success clears the streak
                return response
            logger.warning(
                "Chat vision saw no image (attempt %d/%d): %s",
                attempt,
                self._VISION_RETRY_ATTEMPTS,
                response[:200] or "(empty)",
            )

        self._vision_failure_count += 1
        if self._vision_failure_count >= self._VISION_FAILURE_LIMIT:
            self._vision_actually_works = False
            logger.warning(
                "Chat vision disabled after %d consecutive failed calls",
                self._vision_failure_count,
            )
        return (
            "Vision API could not read the image this time. "
            "Use 'pdf_get_figure_info' for figure captions, "
            "or 'pdf_extract_text'/'pdf_search' to read surrounding text."
        )

    def _clean_vision_response(self, response: str, prompt: str) -> str:
        """Clean up vision API response by removing echoed prompt prefix."""
        if not response or not prompt:
            return response

        # Only strip if the response literally starts with the prompt
        if response.startswith(prompt):
            cleaned = response[len(prompt) :].strip()
            return cleaned if cleaned else response

        return response

    @property
    def vision_actually_works(self) -> bool:
        """Return True if any vision path is available (chat API or VLM)."""
        chat_vision = bool(self._supports_vision and self._vision_actually_works)
        return chat_vision or self._vlm_available

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

            payload = self._structured_payload(prompt, screenshot_b64, mode)
            try:
                data = await self._post_data(payload)
            except httpx.HTTPStatusError as exc:
                if (
                    self.output_mode == "auto"
                    and mode == "native-tools"
                    and self._native_tool_choice == "required"
                    and _required_tool_choice_unsupported(exc)
                ):
                    self._record_structured_fallback(
                        "native-tools:required", "native-tools:auto", exc
                    )
                    self._native_tool_choice = "auto"
                    payload = self._structured_payload(prompt, screenshot_b64, mode)
                    try:
                        data = await self._post_data(payload)
                    except httpx.HTTPStatusError as retry_exc:
                        exc = retry_exc
                    else:
                        raw = _strip_thinking_tags(response_text(data))
                        call = parse_provider_tool_call(data)
                        self._capture_response_metadata(data, len(raw))
                        self._effective_output_mode = mode
                        self._annotate_output_mode(mode)
                        return call
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

    async def _probe_vision(self) -> bool:
        """Send a small image to the chat API; return True if API accepts it."""
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What color is the square in this image? "
                            "Answer with just the color name.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{_probe_image_b64()}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            # Generous budget so reasoning models (which think before answering)
            # still emit a visible answer rather than spending it all on CoT.
            "max_tokens": 1500,
            "temperature": 0.0,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self.api_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.debug("Vision probe got %d: %s", resp.status_code, resp.text[:200])
                    return False
                data = resp.json()
                content = ""
                if "choices" in data:
                    msg = data["choices"][0].get("message", {})
                    content = msg.get("content") or ""
                    if not content:
                        content = msg.get("reasoning_content") or msg.get("reasoning") or ""
                content = _strip_thinking_tags(content)

                # If content is still raw thinking (strip fell back to original
                # because the answer was empty), the model produced no real
                # answer — treat as "cannot see".
                if "<think>" in content.lower():
                    logger.info(
                        "Vision probe: chat API — model produced only thinking, "
                        "no answer. Disabling vision.",
                    )
                    self._vision_actually_works = False
                    return True

                if indicates_no_vision(content):
                    logger.info(
                        "Vision probe: chat API — model cannot see images: %s",
                        content[:100],
                    )
                    self._vision_actually_works = False
                    return True
                if "red" not in content.lower():
                    has_visual = has_visual_content(content)
                    if not has_visual:
                        logger.info(
                            "Vision probe: no visual content (expected 'red', "
                            "got '%s'). Disabling.",
                            content[:100],
                        )
                        self._vision_actually_works = False
                    else:
                        logger.info(
                            "Vision probe: visual indicators present: %s",
                            content[:100],
                        )
                else:
                    logger.info("Vision probe passed: %s", content[:50])
                return True
        except Exception as e:
            logger.debug("Vision probe failed: %s", e)
            return False

    async def _probe_vlm(self) -> bool:
        """Probe a separate VLM endpoint (e.g. MiniMax /v1/coding_plan/vlm)."""
        if not self._vlm_url:
            return False
        # VLM endpoints may reject tiny images; generate a 100×100 red JPEG.
        probe_img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        buf = io.BytesIO()
        probe_img.save(buf, format="JPEG", quality=85)
        probe_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        payload = {
            "prompt": "What color is this image? Answer in one word.",
            "image_url": f"data:image/jpeg;base64,{probe_b64}",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(self._vlm_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.debug("VLM probe got %d: %s", resp.status_code, resp.text[:200])
                    return False
                data = resp.json()
                base_resp = data.get("base_resp", {})
                if base_resp.get("status_code", 0) != 0:
                    logger.debug("VLM probe API error: %s", base_resp)
                    return False
                content = data.get("content", "")
                logger.info("VLM probe response: %s", content[:100])
                # Any non-empty content means the VLM endpoint works
                return bool(content)
        except Exception as e:
            logger.debug("VLM probe failed: %s", e)
            return False

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


def _structured_output_unsupported(exc: httpx.HTTPStatusError, mode: PlannerOutputMode) -> bool:
    """Only downgrade on explicit client-side feature incompatibility.

    Authentication, rate limits, timeouts, and server errors must propagate;
    treating them as capability failures would hide operational incidents.
    """
    response = exc.response
    if response.status_code not in {400, 404, 415, 422}:
        return False
    body = response.text.casefold()
    # Schema/request/model errors are implementation or configuration defects,
    # not evidence that the provider lacks structured-output support.
    non_capability_errors = (
        "invalid schema",
        "schema validation",
        "invalid function",
        "invalid tool definition",
        "unknown model",
        "model not found",
        "invalid request body",
        "malformed request",
        "invalid json",
        "missing required",
        "required field",
    )
    if any(term in body for term in non_capability_errors):
        return False
    explicit_capability_errors = (
        "unsupported",
        "not supported",
        "does not support",
        "unsupported parameter",
        "unknown parameter",
        "unrecognized parameter",
        "unexpected parameter",
        "extra fields not permitted",
    )
    feature_terms = (
        ("tools", "tool_choice", "function", "parallel_tool_calls")
        if mode == "native-tools"
        else ("response_format", "json_schema", "json schema")
    )
    return any(term in body for term in explicit_capability_errors) and any(
        term in body for term in feature_terms
    )


def _required_tool_choice_unsupported(exc: httpx.HTTPStatusError) -> bool:
    """Detect providers that support tools but reject forced tool selection."""
    response = exc.response
    if response.status_code not in {400, 404, 415, 422}:
        return False
    body = response.text.casefold()
    return "tool_choice" in body and any(
        phrase in body
        for phrase in (
            "does not support required",
            "doesn't support required",
            "required or object",
            "required is not supported",
            "only supports auto",
        )
    )
