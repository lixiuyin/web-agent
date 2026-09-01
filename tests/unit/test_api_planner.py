"""Tests for APIPlanner response handling (parsing, reasoning fallback, guards)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from PIL import Image

from webagent.core.models import BrowserState
from webagent.planner import api as api_module
from webagent.planner.api import APIPlanner, _strip_thinking_tags

# ── _strip_thinking_tags (pure) ─────────────────────────────────────────────


def test_strip_closed_think_tags():
    assert _strip_thinking_tags('<think>reason</think>{"tool": "done"}') == '{"tool": "done"}'


def test_strip_unclosed_think_tag():
    # No closing tag — everything from <think> onward is reasoning.
    assert _strip_thinking_tags("answer<think>still thinking") == "answer"


def test_strip_falls_back_to_original_when_all_stripped():
    # If stripping leaves nothing, return the original (don't hand callers "").
    text = "<think>only reasoning, no answer</think>"
    assert _strip_thinking_tags(text) == text.strip()


def test_strip_noop_without_tags():
    assert _strip_thinking_tags('{"tool": "click"}') == '{"tool": "click"}'


# ── _post response parsing (mocked httpx) ───────────────────────────────────


class _FakeResp:
    def __init__(self, data: dict, status: int = 200) -> None:
        self._data = data
        self.status_code = status
        self.text = ""
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://x/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResp:
        return self._resp


def _planner() -> APIPlanner:
    return APIPlanner(api_url="https://x/v1/chat/completions", api_key="k", model_name="m")


def _patch_response(monkeypatch: pytest.MonkeyPatch, data: dict) -> None:
    resp = _FakeResp(data)
    monkeypatch.setattr(api_module.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))


async def test_post_returns_choice_content(monkeypatch):
    _patch_response(monkeypatch, {"choices": [{"message": {"content": '{"tool": "done"}'}}]})
    out = await _planner()._post({})
    assert out == '{"tool": "done"}'


async def test_post_retries_429_before_returning_success(monkeypatch):
    planner = APIPlanner(
        api_url="https://x/v1/chat/completions",
        api_key="k",
        model_name="m",
        transient_retries=2,
        retry_base_seconds=0,
    )
    responses = [
        _FakeResp({"error": "limited"}, status=429),
        _FakeResp({"choices": [{"message": {"content": '{"tool": "done"}'}}]}),
    ]

    async def bounded_post(*args, **kwargs):
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr(planner, "_bounded_post", bounded_post)
    out = await planner._post({})

    assert out == '{"tool": "done"}'
    assert planner.last_call_metadata["transport_retries"] == 1


async def test_post_preserves_retry_count_when_429_is_exhausted(monkeypatch):
    planner = APIPlanner(
        api_url="https://x/v1/chat/completions",
        api_key="k",
        model_name="m",
        transient_retries=2,
        retry_base_seconds=0,
    )
    responses = [_FakeResp({"error": "limited"}, status=429) for _ in range(3)]

    async def bounded_post(*args, **kwargs):
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr(planner, "_bounded_post", bounded_post)

    with pytest.raises(httpx.HTTPStatusError):
        await planner._post({})

    assert planner.last_call_metadata["transport_retries"] == 2


async def test_terminal_confidence_uses_strict_prejudge_schema(monkeypatch):
    planner = _planner()
    payloads: list[dict] = []

    async def post_data(payload, timeout=None):
        del timeout
        payloads.append(payload)
        return {"choices": [{"message": {"content": '{"success_probability":0.35}'}}]}

    monkeypatch.setattr(planner, "_post_data", post_data)

    probability = await planner.estimate_task_success(
        task="task", status="max_steps_reached", history_text="one failed action"
    )

    assert probability == 0.35
    assert payloads[0]["response_format"]["json_schema"]["strict"] is True


async def test_terminal_confidence_falls_back_only_when_json_schema_is_unsupported(
    monkeypatch,
):
    planner = _planner()
    payloads: list[dict] = []

    async def post_data(payload, timeout=None):
        del timeout
        payloads.append(payload.copy())
        if "response_format" in payload:
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            response = httpx.Response(400, request=request, text="response_format is not supported")
            raise httpx.HTTPStatusError("unsupported", request=request, response=response)
        return {"choices": [{"message": {"content": '```json\n{"success_probability":0.1}\n```'}}]}

    monkeypatch.setattr(planner, "_post_data", post_data)

    probability = await planner.estimate_task_success(
        task="task", status="failed", history_text="failed"
    )

    assert probability == 0.1
    assert len(payloads) == 2
    assert "response_format" not in payloads[1]


async def test_post_captures_usage_finish_reason_and_response_length(monkeypatch):
    planner = _planner()
    content = '{"tool": "done"}'
    _patch_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        },
    )

    await planner._post({})

    assert planner.last_call_metadata == {
        "response_length": len(content),
        "finish_reason": "stop",
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "transport_retries": 0,
    }


async def test_post_falls_back_to_reasoning_content(monkeypatch):
    # DeepSeek-style: content empty, the answer is in reasoning_content.
    _patch_response(
        monkeypatch,
        {"choices": [{"message": {"content": "", "reasoning_content": '{"tool": "click"}'}}]},
    )
    out = await _planner()._post({})
    assert out == '{"tool": "click"}'


async def test_post_empty_choices_does_not_raise(monkeypatch):
    # Content-filtered responses return choices=[]; must fall through, not IndexError.
    _patch_response(monkeypatch, {"choices": [], "response": "fallback-text"})
    out = await _planner()._post({})
    assert out == "fallback-text"


async def test_post_strips_thinking_from_choice(monkeypatch):
    _patch_response(
        monkeypatch,
        {"choices": [{"message": {"content": '<think>hmm</think>{"tool": "goto"}'}}]},
    )
    out = await _planner()._post({})
    assert out == '{"tool": "goto"}'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_probe_image_is_valid_base64_jpeg():
    """Regression: the vision-probe image must be valid base64 (the old hardcoded
    constant was malformed, making strict providers 400 and mis-flag vision models)."""
    import base64
    import io

    from PIL import Image

    from webagent.planner.api import _probe_image_b64

    raw = base64.b64decode(_probe_image_b64(), validate=True)  # raises if invalid
    img = Image.open(io.BytesIO(raw))
    img.load()
    assert img.format == "JPEG"
    assert img.size[0] > 1 and img.size[1] > 1


async def test_plan_action_sends_dom_and_screenshot_in_same_model_request():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "done", "parameters": {"summary": "ok"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="<main><button>Go</button></main>",
        url="https://example.com",
        title="Example",
        timestamp="2024-01-01",
    )

    tool_call = await planner.plan_action(
        task="click Go",
        browser_state=state,
        history_text="No previous actions.",
        available_tools="click, done",
    )

    assert tool_call is not None
    user_content = payloads[0]["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "text"
    assert "PAGE:\n<main><button>Go</button></main>" in user_content[0]["text"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_plan_action_sends_dom_without_blank_screenshot():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "done", "parameters": {"summary": "ok"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "white"),
        dom_summary="<body>blank browser shell</body>",
        url="about:blank",
        title="",
        timestamp="2024-01-01",
    )

    await planner.plan_action(
        task="task",
        browser_state=state,
        history_text="",
        available_tools="done",
    )

    user_content = payloads[0]["messages"][1]["content"]
    assert isinstance(user_content, str)
    assert "PAGE:\n<body>blank browser shell</body>" in user_content
    assert "image_url" not in user_content


async def test_plan_action_auto_omits_screenshot_for_text_rich_page():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "done", "parameters": {"summary": "ok"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="<main>" + ("Search result with title, URL, and snippet. " * 20) + "</main>",
        url="https://www.bing.com/search?q=report",
        title="Search",
        timestamp="2024-01-01",
    )

    await planner.plan_action(
        task="find a report",
        browser_state=state,
        history_text="No previous actions.",
        available_tools="goto, done",
    )

    assert isinstance(payloads[0]["messages"][1]["content"], str)


async def test_plan_action_visual_strategy_keeps_text_rich_screenshot():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "done", "parameters": {"summary": "ok"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="<main>" + ("Ambiguous controls. " * 30) + "</main>",
        url="https://example.com/app",
        title="App",
        timestamp="2024-01-01",
    )

    await planner.plan_action(
        task="click the chart control",
        browser_state=state,
        history_text="CONTROLLER STRATEGY HINT: visual-grounding",
        available_tools="click, done",
    )

    assert isinstance(payloads[0]["messages"][1]["content"], list)


async def test_plan_action_sends_configured_reasoning_effort_without_reasoning_content():
    planner = APIPlanner(
        api_url="https://openrouter.example/v1/chat/completions",
        api_key="k",
        model_name="reasoning-model",
        reasoning_effort="low",
    )
    planner._supports_vision = False
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "done", "parameters": {"summary": "ok"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    await planner.plan_action(
        task="task",
        browser_state=BrowserState(
            dom_summary="<body></body>",
            url="about:blank",
            title="",
            timestamp="2024-01-01",
        ),
        history_text="",
        available_tools="done",
    )

    assert payloads[0]["reasoning"] == {"effort": "low", "exclude": True}


async def test_plan_action_omits_redundant_local_pdf_preview():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "pdf_analyze_figure", "parameters": {"path": "paper.pdf"}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="PDF viewer",
        url="file:///run/artifacts/paper.pdf",
        title="paper.pdf",
        timestamp="2024-01-01",
    )

    await planner.plan_action(
        task="analyze Figure 1",
        browser_state=state,
        history_text='Step 1: download_pdf({"url":"https://example.test/paper.pdf"}) -> success',
        available_tools="pdf_analyze_figure, done",
    )

    assert isinstance(payloads[0]["messages"][1]["content"], str)


async def test_plan_action_keeps_local_html_screenshot_without_artifact_history():
    planner = _planner()
    planner._supports_vision = True
    payloads: list[dict] = []

    async def capture_post(payload: dict) -> str:
        payloads.append(payload)
        return '{"tool": "click", "parameters": {}}'

    planner._post = capture_post  # type: ignore[method-assign]
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="<button>Run</button>",
        url="file:///fixtures/app.html",
        title="fixture",
        timestamp="2024-01-01",
    )

    await planner.plan_action(
        task="click Run",
        browser_state=state,
        history_text="No previous actions.",
        available_tools="click, done",
    )

    assert isinstance(payloads[0]["messages"][1]["content"], list)


# ── _bounded_post hard wall-clock timeout (trickle/hang guard) ───────────────


class _HangingClient:
    """AsyncClient whose ``post`` never returns within the hard timeout.

    Mimics a server that holds the connection open (or trickles bytes) so that
    httpx's per-read timeout never fires.
    """

    async def __aenter__(self) -> _HangingClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(30)  # far longer than the hard timeout under test
        raise AssertionError("post should have been cancelled by the hard timeout")


async def test_bounded_post_enforces_hard_timeout(monkeypatch):
    # A hanging/trickling server must be bounded by hard_timeout, not run until
    # the whole task budget is exhausted.
    monkeypatch.setattr(api_module.httpx, "AsyncClient", lambda *a, **k: _HangingClient())
    planner = APIPlanner(api_url="https://x", api_key="k", model_name="m", timeout=1)
    planner.hard_timeout = 0.05  # keep the test fast
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await planner._bounded_post("https://x", {}, {})


def test_hard_timeout_never_below_read_timeout():
    # A misconfigured hard_timeout < timeout would let a single per-read window
    # exceed the wall-clock cap; the planner clamps the cap up to the read timeout.
    planner = APIPlanner(api_url="u", api_key="k", model_name="m", timeout=120, hard_timeout=30)
    assert planner.hard_timeout == 120
