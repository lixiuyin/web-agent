"""Tests for APIPlanner vision routing and probing."""

from __future__ import annotations

from typing import Any

import pytest

from webagent.planner._vision_heuristics import has_visual_content, indicates_no_vision
from webagent.planner.api import APIPlanner, _detect_vlm_url


def _planner(**kw: Any) -> APIPlanner:
    defaults: dict[str, Any] = {"api_url": "https://api.test/v1/chat/completions", "api_key": "k"}
    defaults.update(kw)
    return APIPlanner(**defaults)


class TestDetectVlmUrl:
    def test_minimaxi(self) -> None:
        assert (
            _detect_vlm_url("https://api.minimaxi.com/v1/chat/completions")
            == "https://api.minimaxi.com/v1/coding_plan/vlm"
        )

    def test_minimax_io(self) -> None:
        assert _detect_vlm_url("https://api.minimax.io/v1/chat/completions") is not None

    def test_other_provider(self) -> None:
        assert _detect_vlm_url("https://api.openai.com/v1/chat/completions") is None

    def test_planner_stores_vlm_url(self) -> None:
        assert _planner(api_url="https://api.minimaxi.com/v1/chat/completions")._vlm_url


class TestVisionRouting:
    async def test_analyze_image_without_any_vision(self) -> None:
        planner = _planner()
        planner._supports_vision = False
        result = await planner.analyze_image(_image(), "what?")
        assert "Vision API is not available" in result

    async def test_analyze_image_uses_vlm_endpoint_when_available(self) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")
        planner._supports_vision = False
        planner._vlm_available = True

        calls: list[tuple[str, dict[str, Any]]] = []

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"base_resp": {"status_code": 0}, "content": "A red square"}

        async def fake_bounded_post(url: str, payload: dict[str, Any], headers: dict[str, str]):
            calls.append((url, payload))
            return _Resp()

        planner._bounded_post = fake_bounded_post  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "color?")
        assert result == "A red square"
        assert calls[0][0] == planner._vlm_url

    async def test_analyze_image_vlm_error_disables_endpoint(self) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")
        planner._supports_vision = False
        planner._vlm_available = True

        class _Resp:
            status_code = 500
            text = "boom"

            def json(self) -> dict[str, Any]:
                return {}

        async def fake_bounded_post(url: str, payload: dict[str, Any], headers: dict[str, str]):
            return _Resp()

        planner._bounded_post = fake_bounded_post  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "color?")
        assert "VLM API returned error 500" in result
        assert planner._vlm_available is False

    async def test_analyze_image_vlm_api_level_error(self) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")
        planner._supports_vision = False
        planner._vlm_available = True

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"base_resp": {"status_code": 1001, "status_msg": "rate limited"}}

        async def fake_bounded_post(url: str, payload: dict[str, Any], headers: dict[str, str]):
            return _Resp()

        planner._bounded_post = fake_bounded_post  # type: ignore[method-assign]
        assert "rate limited" in await planner.analyze_image(_image(), "color?")

    async def test_analyze_image_vlm_empty_content(self) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")
        planner._supports_vision = False
        planner._vlm_available = True

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"base_resp": {"status_code": 0}, "content": ""}

        async def fake_bounded_post(url: str, payload: dict[str, Any], headers: dict[str, str]):
            return _Resp()

        planner._bounded_post = fake_bounded_post  # type: ignore[method-assign]
        assert "empty response" in await planner.analyze_image(_image(), "color?")

    async def test_analyze_image_chat_success(self) -> None:
        planner = _planner()
        planner._supports_vision = True

        async def fake_analyze_chat(b64: str, question: str) -> str:
            return f"chat analysis for: {question}"

        planner._analyze_image_chat = fake_analyze_chat  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "what is shown?")
        assert result == "chat analysis for: what is shown?"

    async def test_analyze_image_resizes_large_images(self) -> None:
        planner = _planner()
        planner._supports_vision = True
        seen: dict[str, int] = {}

        async def fake_analyze_chat(b64: str, question: str) -> str:
            return "ok"

        original = fake_analyze_chat

        async def sized_chat(b64: str, question: str) -> str:
            # b64 length reflects the (resized) image bytes
            seen["b64_len"] = len(b64)
            return await original(b64, question)

        planner._analyze_image_chat = sized_chat  # type: ignore[method-assign]
        big = _image(4000, 1000)
        await planner.analyze_image(big, "q")
        assert "b64_len" in seen  # routed through chat path


class TestChatVisionFailure:
    async def test_detail_request_uses_configured_concise_budget(self) -> None:
        planner = _planner(
            vision_max_tokens=1777,
            vision_brief_max_tokens=666,
            vision_max_words=321,
        )
        planner._supports_vision = True
        payloads: list[dict[str, Any]] = []

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            payloads.append(payload)
            return "The diagram shows three connected modules."

        planner._post = fake_post  # type: ignore[method-assign]
        await planner.analyze_image(_image(), "Describe the purpose and key findings in detail")

        assert payloads[0]["max_tokens"] == 1777
        prompt = payloads[0]["messages"][0]["content"][0]["text"]
        assert "under 321 words" in prompt
        assert "Omit meta-commentary" in prompt

    async def test_brief_request_uses_smaller_budget(self) -> None:
        planner = _planner(vision_max_tokens=1777, vision_brief_max_tokens=666)
        planner._supports_vision = True
        payloads: list[dict[str, Any]] = []

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            payloads.append(payload)
            return "Blue."

        planner._post = fake_post  # type: ignore[method-assign]
        await planner.analyze_image(_image(), "Color?")

        assert payloads[0]["max_tokens"] == 666

    async def test_single_no_vision_call_keeps_vision_enabled(self) -> None:
        planner = _planner()
        planner._supports_vision = True
        calls = 0

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            nonlocal calls
            calls += 1
            return "I cannot see any image in this request."

        planner._post = fake_post  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "color?")
        assert "could not read the image" in result
        assert calls == planner._VISION_RETRY_ATTEMPTS  # retried within the call
        assert planner.vision_actually_works is True  # one blip does not disable

    async def test_empty_chat_response_retries_and_reports_failure(self) -> None:
        """A blank model response must not be returned as a successful (empty) analysis."""
        planner = _planner()
        planner._supports_vision = True
        calls = 0

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            nonlocal calls
            calls += 1
            return ""

        planner._post = fake_post  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "color?")
        assert "could not read the image" in result
        assert calls == planner._VISION_RETRY_ATTEMPTS  # blank is retried, not returned
        assert planner.vision_actually_works is True  # one blip does not disable

    async def test_repeated_empty_chat_responses_disable_vision(self) -> None:
        """Repeated blank responses latch chat vision off, like repeated 'cannot see'."""
        planner = _planner()
        planner._supports_vision = True

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            return ""

        planner._post = fake_post  # type: ignore[method-assign]
        for _ in range(planner._VISION_FAILURE_LIMIT):
            await planner.analyze_image(_image(), "color?")
        assert planner.vision_actually_works is False

    async def test_repeated_no_vision_calls_disable_chat_vision(self) -> None:
        planner = _planner()
        planner._supports_vision = True

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            return "I cannot see any image in this request."

        planner._post = fake_post  # type: ignore[method-assign]
        for _ in range(planner._VISION_FAILURE_LIMIT):
            await planner.analyze_image(_image(), "color?")
        assert planner.vision_actually_works is False

    async def test_success_resets_failure_streak(self) -> None:
        planner = _planner()
        planner._supports_vision = True
        planner._vision_failure_count = planner._VISION_FAILURE_LIMIT - 1
        responses = iter(["The image shows a red square"])

        async def fake_post(payload: dict[str, Any], timeout: int | None = None) -> str:
            return next(responses)

        planner._post = fake_post  # type: ignore[method-assign]
        result = await planner.analyze_image(_image(), "color?")
        assert "red square" in result
        assert planner._vision_failure_count == 0
        assert planner.vision_actually_works is True

    async def test_clean_vision_response_strips_echoed_prompt(self) -> None:
        planner = _planner()
        prompt = "You are an image analysis assistant. Question: color?"
        assert planner._clean_vision_response(prompt + " Red.", prompt) == "Red."
        # Response not starting with prompt is untouched
        assert planner._clean_vision_response("Blue.", prompt) == "Blue."
        # Echoed-only response falls back to original
        assert planner._clean_vision_response(prompt, prompt) == prompt

    async def test_indicates_no_vision(self) -> None:
        assert indicates_no_vision("There is no image attached")
        assert not indicates_no_vision("The image shows a red square")
        assert not indicates_no_vision("")


class TestHasVisualContent:
    def test_color_words_count(self) -> None:
        assert has_visual_content("crimson red tones")

    def test_short_alpha_answer_counts(self) -> None:
        assert has_visual_content("Red")

    def test_medium_description_pattern(self) -> None:
        assert has_visual_content("It is a solid bar of metal")

    def test_long_non_visual_text_fails(self) -> None:
        assert not has_visual_content(
            "The quarterly financial projections were adjusted downward " * 5
        )

    def test_empty(self) -> None:
        assert not has_visual_content("")


class TestLoad:
    async def test_load_with_vision_supported(self) -> None:
        planner = _planner()

        async def fake_probe() -> bool:
            return True

        planner._probe_vision = fake_probe  # type: ignore[method-assign]
        await planner.load()
        assert planner._supports_vision is True
        assert planner.vision_actually_works

    async def test_load_text_only(self) -> None:
        planner = _planner()

        async def fake_probe() -> bool:
            return False

        planner._probe_vision = fake_probe  # type: ignore[method-assign]
        await planner.load()
        assert planner._supports_vision is False
        assert not planner.vision_actually_works

    async def test_load_probes_vlm_when_chat_vision_dead(self) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")

        async def fake_probe() -> bool:
            planner._vision_actually_works = False  # accepted but blind
            return True

        async def fake_probe_vlm() -> bool:
            return True

        planner._probe_vision = fake_probe  # type: ignore[method-assign]
        planner._probe_vlm = fake_probe_vlm  # type: ignore[method-assign]
        await planner.load()
        assert planner._vlm_available is True
        assert planner.vision_actually_works  # via VLM endpoint


def _image(width: int = 100, height: int = 100) -> Any:
    from PIL import Image

    return Image.new("RGB", (width, height), color="red")


class TestProbeVisionDecisions:
    async def test_probe_no_vision_answer_sets_blind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        planner = _planner()

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"choices": [{"message": {"content": "no image was provided"}}]}

        class _Client:
            def __init__(self, timeout: int = 0) -> None: ...

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: Any) -> None: ...

            async def post(self, url: str, **kwargs: Any) -> _Resp:
                return _Resp()

        import webagent.planner.api as api_mod

        monkeypatch.setattr(api_mod.httpx, "AsyncClient", _Client)
        result = await planner._probe_vision()
        assert result is True  # API accepts the format
        assert planner._vision_actually_works is False  # but the model is blind

    async def test_probe_red_answer_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        planner = _planner()

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"choices": [{"message": {"content": "Red"}}]}

        class _Client:
            def __init__(self, timeout: int = 0) -> None: ...

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: Any) -> None: ...

            async def post(self, url: str, **kwargs: Any) -> _Resp:
                return _Resp()

        import webagent.planner.api as api_mod

        monkeypatch.setattr(api_mod.httpx, "AsyncClient", _Client)
        assert await planner._probe_vision() is True
        assert planner._vision_actually_works

    async def test_probe_http_error_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        planner = _planner()

        class _Client:
            def __init__(self, timeout: int = 0) -> None: ...

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: Any) -> None: ...

            async def post(self, url: str, **kwargs: Any) -> None:
                raise ConnectionError("network down")

        import webagent.planner.api as api_mod

        monkeypatch.setattr(api_mod.httpx, "AsyncClient", _Client)
        assert await planner._probe_vision() is False


class TestProbeVlm:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        planner = _planner(api_url="https://api.minimaxi.com/v1/chat/completions")

        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {"base_resp": {"status_code": 0}, "content": "red"}

        class _Client:
            def __init__(self, timeout: int = 0) -> None: ...

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: Any) -> None: ...

            async def post(self, url: str, **kwargs: Any) -> _Resp:
                return _Resp()

        import webagent.planner.api as api_mod

        monkeypatch.setattr(api_mod.httpx, "AsyncClient", _Client)
        assert await planner._probe_vlm() is True

    async def test_no_vlm_url(self) -> None:
        assert await _planner()._probe_vlm() is False
