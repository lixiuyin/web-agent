"""Tests for APIPlanner response handling (parsing, reasoning fallback, guards)."""

from __future__ import annotations

import asyncio

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

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        return None


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
