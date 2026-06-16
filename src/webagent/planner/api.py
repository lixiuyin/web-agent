"""Remote API planner (OpenAI-compatible endpoints)."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
from typing import Any

import httpx
from PIL import Image

from webagent.core.models import BrowserState, ToolCall
from webagent.planner.base import SYSTEM_PROMPT, build_prompt, parse_llm_response
from webagent.planner.enhanced_base import (
    ENHANCED_SYSTEM_PROMPT,
    build_enhanced_prompt,
    parse_enhanced_response,
)

# Matches <think>...</think> blocks produced by reasoning models (DeepSeek, GLM-Z1, etc.)
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Matches unclosed <think> tags (model didn't emit </think>)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)

logger = logging.getLogger("webagent")


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

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model_name: str = "glm-4.7",
        timeout: int = 120,
        temperature: float = 0.7,
        use_structured_output: bool = False,
        max_tokens: int = 4096,
        hard_timeout: int = 300,
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
        self.temperature = temperature
        self.use_structured_output = use_structured_output
        # Cap output length. Generous enough for reasoning models (which spend
        # tokens thinking before emitting the action JSON) without truncation.
        self.max_tokens = max_tokens
        self._supports_vision: bool | None = None  # chat API accepts images?
        self._vision_actually_works: bool = True  # chat API vision produces real results?
        self._vlm_url: str | None = _detect_vlm_url(api_url)  # separate VLM endpoint
        self._vlm_available: bool = False  # probed during load()

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
        # Choose prompt builder based on configuration
        if self.use_structured_output:
            prompt, screenshot_b64 = build_enhanced_prompt(
                task, browser_state, history_text, available_tools
            )
        else:
            prompt, screenshot_b64 = build_prompt(
                task, browser_state, history_text, available_tools
            )

        if not self._supports_vision:
            screenshot_b64 = None
        logger.info(
            "Planner request context: dom_chars=%d screenshot_captured=%s screenshot_sent=%s",
            len(browser_state.dom_summary),
            browser_state.screenshot is not None,
            screenshot_b64 is not None,
        )
        raw = await self._call(prompt, screenshot_b64)

        # Parse response based on configuration
        if self.use_structured_output:
            enhanced = parse_enhanced_response(raw)
            if enhanced:
                return ToolCall(
                    tool_name=enhanced.tool_name,
                    parameters=enhanced.parameters,
                    reasoning=enhanced.reasoning,
                )
            return None
        else:
            return parse_llm_response(raw)

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
        """Analyze an image via the chat completions API (inline image)."""
        prompt_with_instruction = (
            "You are an image analysis assistant. Carefully observe the image "
            "and answer the user's question. If the content cannot be clearly "
            "seen or determined, state it honestly. "
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
            "max_tokens": 2000,
        }
        response = await self._post(payload)
        response = self._clean_vision_response(response, prompt_with_instruction)

        if self._indicates_no_vision(response):
            logger.warning(
                "Chat vision failed — model cannot see image. Response: %s",
                response[:200],
            )
            self._vision_actually_works = False
            return (
                "Vision API is not functioning properly. "
                "Use 'pdf_get_figure_info' for figure captions, "
                "or 'pdf_extract_text'/'pdf_search' to read surrounding text."
            )
        return response

    def _clean_vision_response(self, response: str, prompt: str) -> str:
        """Clean up vision API response by removing echoed prompt prefix."""
        if not response or not prompt:
            return response

        # Only strip if the response literally starts with the prompt
        if response.startswith(prompt):
            cleaned = response[len(prompt) :].strip()
            return cleaned if cleaned else response

        return response

    @staticmethod
    def _indicates_no_vision(response: str) -> bool:
        """Return True if the response suggests the model cannot see the image."""
        if not response:
            return False
        lower = response.lower()
        no_vision_phrases = [
            "i don't see any image",
            "i don't see an image",
            "i cannot see any image",
            "i cannot see the image",
            "no image attached",
            "no image provided",
            "no image was provided",
            "i'm unable to view",
            "i am unable to view",
            "i can't view the image",
            "i cannot view the image",
            "i don't have the ability to view",
            "i do not have the ability to view",
            "i'm not able to see",
            "i am not able to see",
            "there is no image",
            "image is not visible",
            "unable to see the image",
            "cannot analyze images",
            "i cannot analyze the image",
        ]
        return any(phrase in lower for phrase in no_vision_phrases)

    def _has_visual_content(self, response: str) -> bool:
        """Check if response contains visual analysis indicators."""
        if not response:
            return False

        response_lower = response.lower()

        # Visual content indicators - words that suggest the model is describing an image
        # Also include simple color answers which are valid vision responses
        visual_indicators = [
            # Colors
            "red",
            "blue",
            "green",
            "yellow",
            "black",
            "white",
            "orange",
            "purple",
            "pink",
            "brown",
            "gray",
            "grey",
            "color",
            # Shapes and visual elements
            "shows",
            "shows a",
            "depicts",
            "displays",
            "illustrates",
            "presents",
            "figure",
            "chart",
            "graph",
            "image",
            "diagram",
            "plot",
            "rectangle",
            "square",
            "circle",
            "left",
            "right",
            "top",
            "bottom",
            "center",
            # Descriptive language
            "the image",
            "this figure",
            "the chart",
            "the graph",
            "the picture",
            "we can see",
            "visible",
            "appears to be",
            "see the",
            "image is",
        ]

        # Check for at least one visual indicator
        has_indicator = any(indicator in response_lower for indicator in visual_indicators)

        # Also accept short responses that are likely color/object answers
        # (e.g., "Red", "A cat", "Blue sky")
        if not has_indicator and len(response) < 10 and response.isalpha():
            # Very short answers like color names are valid vision responses
            return True

        # Also accept medium-length responses that describe visual content
        # (e.g., "It is a green rectangle.")
        if not has_indicator and 10 <= len(response) <= 150:
            # Check for common visual description patterns
            visual_patterns = ["is a", "is an", "consists of", "contains", "made of", "solid"]
            if any(pattern in response_lower for pattern in visual_patterns):
                return True

        return has_indicator

    @property
    def vision_actually_works(self) -> bool:
        """Return True if any vision path is available (chat API or VLM)."""
        chat_vision = bool(self._supports_vision and self._vision_actually_works)
        return chat_vision or self._vlm_available

    # -- internals --------------------------------------------------------

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
                    if not content and msg.get("reasoning_content"):
                        content = msg["reasoning_content"]
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

                if self._indicates_no_vision(content):
                    logger.info(
                        "Vision probe: chat API — model cannot see images: %s",
                        content[:100],
                    )
                    self._vision_actually_works = False
                    return True
                if "red" not in content.lower():
                    has_visual = self._has_visual_content(content)
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

        # Choose system prompt based on configuration
        system_prompt = ENHANCED_SYSTEM_PROMPT if self.use_structured_output else SYSTEM_PROMPT

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        return await self._post(payload)

    async def _bounded_post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        """POST with a per-read timeout AND a hard wall-clock cap.

        httpx's read timeout resets on every received byte, so it cannot bound a
        server that trickles data. ``asyncio.wait_for`` enforces a true upper
        bound (``hard_timeout``); on expiry it raises ``TimeoutError``, which
        callers let propagate so the agent records a failed step and recovers.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await asyncio.wait_for(
                client.post(url, headers=headers, json=payload),
                timeout=self.hard_timeout,
            )

    async def _post(self, payload: dict[str, Any]) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        resp = await self._bounded_post(self.api_url, payload, headers)
        if resp.status_code != 200:
            logger.error("API %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()

        # Log response structure for debugging
        logger.debug(
            "API response keys: %s", list(data.keys()) if isinstance(data, dict) else type(data)
        )

        if data.get("choices"):
            msg = data["choices"][0].get("message", {})
            content = msg.get("content") or ""
            # Some APIs put the thinking chain in a separate field;
            # if content is empty fall back to reasoning_content.
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
            content = _strip_thinking_tags(content)
            logger.debug("API response length: %d chars", len(content))
            return content
        # Handle alternative response formats
        response = data.get("response", "")
        if not response and "data" in data:
            response = data.get("data", {}).get("content", "")
        response = _strip_thinking_tags(response)
        logger.debug("API response length: %d chars", len(response))
        return response
