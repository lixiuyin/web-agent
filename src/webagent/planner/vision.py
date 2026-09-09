"""Session vision capability detection and image analysis routing."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Protocol

import httpx
from PIL import Image

from webagent.planner._vision_heuristics import has_visual_content, indicates_no_vision
from webagent.planner.provider_response import _strip_thinking_tags

logger = logging.getLogger("webagent")
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


class VisionBackend(Protocol):
    """Configuration and request operations shared with the planning transport."""

    api_url: str
    api_key: str
    model_name: str
    temperature: float
    hard_timeout: int
    vision_max_tokens: int
    vision_brief_max_tokens: int
    vision_max_words: int

    async def _post(self, payload: dict[str, Any], timeout: int | None = None) -> str: ...

    async def _bounded_post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: int | None = None,
    ) -> httpx.Response: ...


class VisionSupport:
    """Own vision capability state, probing, retries, and VLM/chat routing."""

    _VISION_RETRY_ATTEMPTS = 2
    _VISION_FAILURE_LIMIT = 2

    def __init__(self, backend: VisionBackend) -> None:
        self._backend = backend
        self._supports_vision: bool | None = None
        self._vision_actually_works = True
        self._vision_failure_count = 0
        self._vlm_url = _detect_vlm_url(backend.api_url)
        self._vlm_available = False

    @property
    def supports_images(self) -> bool:
        """Whether the chat endpoint accepts image input."""
        return bool(self._supports_vision)

    async def load(self) -> None:
        """Probe the API to detect vision support."""
        self._supports_vision = await self._probe_vision()
        if self._supports_vision:
            if self._vision_actually_works:
                logger.info("Model %s: vision supported (chat API)", self._backend.model_name)
            else:
                logger.info(
                    "Model %s: vision format accepted but model cannot see images",
                    self._backend.model_name,
                )
        else:
            self._vision_actually_works = False
            logger.info("Model %s: text-only (chat API)", self._backend.model_name)

        # If chat API vision doesn't work, try a separate VLM endpoint.
        if not self.vision_actually_works and self._vlm_url:
            self._vlm_available = await self._probe_vlm()
            if self._vlm_available:
                logger.info(
                    "Model %s: VLM endpoint available at %s",
                    self._backend.model_name,
                    self._vlm_url,
                )
            else:
                logger.info(
                    "Model %s: VLM endpoint probe failed (%s)",
                    self._backend.model_name,
                    self._vlm_url,
                )

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
            "Authorization": f"Bearer {self._backend.api_key}",
        }
        logger.debug("analyze_image_vlm: sending request to %s", self._vlm_url)
        resp = await self._backend._bounded_post(self._vlm_url, payload, headers)
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
                f"Keep the answer under {self._backend.vision_max_words} words. "
            )
            max_tokens = self._backend.vision_max_tokens
        else:
            directive = (
                "Answer concisely, in a few short paragraphs, without showing your reasoning. "
            )
            max_tokens = min(self._backend.vision_max_tokens, self._backend.vision_brief_max_tokens)

        prompt_with_instruction = (
            "You are an image analysis assistant. Carefully observe the image "
            "and answer the user's question. If the content cannot be clearly "
            "seen or determined, state it honestly. "
            f"{directive}"
            f"\n\nUser question: {question}"
        )
        payload: dict[str, Any] = {
            "model": self._backend.model_name,
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
            "temperature": max(0.3, self._backend.temperature),
            "max_tokens": max_tokens,
        }

        for attempt in range(1, self._VISION_RETRY_ATTEMPTS + 1):
            response = await self._backend._post(payload, timeout=self._backend.hard_timeout)
            metadata = getattr(self._backend, "last_call_metadata", {})
            if isinstance(metadata, dict) and metadata.get("finish_reason") == "length":
                raise ValueError("Vision response truncated at token limit; no complete analysis")
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

    async def _probe_vision(self) -> bool:
        """Send a small image to the chat API; return True if API accepts it."""
        payload: dict[str, Any] = {
            "model": self._backend.model_name,
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
            "Authorization": f"Bearer {self._backend.api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self._backend.api_url, headers=headers, json=payload)
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
            "Authorization": f"Bearer {self._backend.api_key}",
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
