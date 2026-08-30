"""Runtime integration tests for atomic checkpoint and resume semantics."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from webagent.agent.checkpoint import CheckpointStore, PendingAction, checkpoint_fingerprint
from webagent.agent.loop import (
    WebAgent,
    _checkpoint_step,
    _checkpoint_tab_url,
    _LoopState,
    _made_progress,
    _planner_repair_hint,
    _policy_was_denied,
    _replay_policy,
)
from webagent.agent.state import PlanningState
from webagent.agent.strategy import StrategyManager
from webagent.cli import _apply_resume_arguments, parse_args
from webagent.core.config import AgentConfig
from webagent.core.models import AgentStep, BrowserState, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout


class _Page:
    url = "about:blank"


class _Browser:
    def __init__(self) -> None:
        self.page = _Page()
        self.restored: dict[str, Any] | None = None

    async def export_checkpoint_state(self, *, include_storage: bool = False) -> dict[str, Any]:
        assert include_storage is False
        return {"schema_version": 1, "tabs": [self.page.url], "active_index": 0}

    async def restore_checkpoint_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self.restored = state
        self.page.url = state["tabs"][state["active_index"]]
        return {"success": True}

    async def check_captcha(self) -> dict[str, Any]:
        return {"detected": False}


class _Planner:
    vision_actually_works = True

    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = iter(calls)
        self.histories: list[str] = []

    async def plan_action(self, **kwargs: Any) -> ToolCall | None:
        self.histories.append(str(kwargs.get("history_text", "")))
        return next(self.calls)


class _Executor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def get_tool_descriptions(self) -> str:
        return "search, done"

    def reset_policy(self, _task: str) -> None:
        return None

    def export_policy_state(self) -> None:
        return None

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call.tool_name)
        data = (
            {"summary": "finished"}
            if call.tool_name == "done"
            else {"results": [{"title": "grounded"}], "url": "https://example.test"}
        )
        return ToolResult(success=True, tool_name=call.tool_name, data=data)


class _ArtifactExecutor(_Executor):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call.tool_name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"pdf")
        return ToolResult(
            success=True,
            tool_name=call.tool_name,
            data={"path": str(self.path), "filename": self.path.name},
        )


class _SensitiveExecutor(_Executor):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def export_policy_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "policy": "test_policy",
            "task": self.secret,
            "password": self.secret,
            "observed_urls": {
                f"https://user:pw@example.test/private?token={self.secret}": {"text": self.secret}
            },
            "downloaded_paths": {f"/Users/private/{self.secret}.pdf": {}},
        }

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call.tool_name)
        return ToolResult(
            success=True,
            tool_name=call.tool_name,
            data={
                "text": self.secret,
                "value": self.secret,
                "nested": {"value": f"https://example.test/result?token={self.secret}"},
                "path": f"/Users/private/{self.secret}.txt",
            },
            audit={"reason": self.secret, "password": self.secret},
        )


def _config(output: Path) -> AgentConfig:
    return AgentConfig(
        _env_file=None,
        use_vllm=False,
        output_dir=output,
        max_steps=2,
        captcha_pause=False,
        post_action_wait_ms=0,
    )


def _state() -> BrowserState:
    return BrowserState(
        screenshot=None,
        dom_summary="body",
        url="about:blank",
        title="",
        timestamp="now",
    )


async def test_resume_continues_history_and_preserves_trace_run_id(tmp_path: Path) -> None:
    output = tmp_path / "run"
    first = WebAgent(
        _Planner([ToolCall(tool_name="search")]), _Browser(), _Executor(), _config(output)
    )
    first._observe = _async_state  # type: ignore[method-assign]
    result = await first.run("find evidence", max_steps=1)
    assert result.status == "max_steps_reached"
    layout = RunLayout.from_root(output)
    trace_before = json.loads(layout.trace_path.read_text())
    checkpoint = layout.checkpoint_path
    layout.legacy_checkpoint_path.write_bytes(checkpoint.read_bytes())
    checkpoint.unlink()
    layout.manifest_path.unlink()

    second_browser = _Browser()
    second = WebAgent(
        _Planner([ToolCall(tool_name="done", parameters={"summary": "finished"})]),
        second_browser,
        _Executor(),
        _config(tmp_path / "wrong-output"),
    )
    second._observe = _async_state  # type: ignore[method-assign]
    resumed = await second.run("find evidence", resume_from=layout.legacy_checkpoint_path)

    trace_after = json.loads(layout.trace_path.read_text())
    assert resumed.status == "completed"
    assert [step.step_number for step in resumed.history] == [1, 2]
    assert trace_after["run_id"] == trace_before["run_id"]
    assert trace_after["resume_count"] == 1
    assert trace_after["resumed_from_checkpoint"] is True
    assert layout.checkpoint_path.is_file()
    assert json.loads(layout.manifest_path.read_text())["run_id"] == trace_before["run_id"]
    assert second_browser.restored is not None


async def _async_state() -> BrowserState:
    return _state()


async def test_unresolved_interaction_pending_action_blocks_without_replay(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    first = WebAgent(
        _Planner([ToolCall(tool_name="search")]), _Browser(), _Executor(), _config(output)
    )
    first._observe = _async_state  # type: ignore[method-assign]
    await first.run("submit safely", max_steps=1)
    store = CheckpointStore(RunLayout.from_root(output).checkpoint_path)
    checkpoint = store.load()
    store.save(
        checkpoint.model_copy(
            update={
                "status": "interrupted",
                "pending_action": PendingAction(
                    tool_name="click",
                    parameters_sha256=checkpoint_fingerprint({"selector": "#submit"}),
                    external_effect="none_or_reversible",
                    replay_policy="reconcile",
                ),
            }
        )
    )
    executor = _Executor()
    agent = WebAgent(_Planner([]), _Browser(), executor, _config(output))
    agent._observe = _async_state  # type: ignore[method-assign]

    result = await agent.run("submit safely", resume_from=store.path)

    assert result.status == "blocked"
    assert executor.executed == []
    assert any(event["type"] == "resume_pending_action_blocked" for event in result.events)


def test_css_submit_interaction_is_never_marked_safe() -> None:
    call = ToolCall(tool_name="click", parameters={"selector": "#submit"})
    assert _replay_policy(call, approval_required=False) == "reconcile"
    assert _replay_policy(call, approval_required=True) == "forbid"


async def test_completed_checkpoint_cannot_be_resumed(tmp_path: Path) -> None:
    output = tmp_path / "run"
    agent = WebAgent(
        _Planner([ToolCall(tool_name="done", parameters={"summary": "done"})]),
        _Browser(),
        _Executor(),
        _config(output),
    )
    agent._observe = _async_state  # type: ignore[method-assign]
    await agent.run("finish")

    with pytest.raises(ValueError, match="terminal"):
        await agent.run("finish", resume_from=RunLayout.from_root(output).checkpoint_path)


async def test_resume_rejects_missing_explicit_tool_artifact(tmp_path: Path) -> None:
    output = tmp_path / "run"
    artifact = output / "artifacts" / "report.pdf"
    first = WebAgent(
        _Planner([ToolCall(tool_name="download_pdf")]),
        _Browser(),
        _ArtifactExecutor(artifact),
        _config(output),
    )
    first._observe = _async_state  # type: ignore[method-assign]
    await first.run("read report", max_steps=1)
    checkpoint_path = RunLayout.from_root(output).checkpoint_path
    assert CheckpointStore(checkpoint_path).load().artifacts[0].path == "artifacts/report.pdf"
    artifact.unlink()

    resumed = WebAgent(_Planner([]), _Browser(), _Executor(), _config(output))
    with pytest.raises(ValueError, match="missing or changed"):
        await resumed.run("read report", resume_from=checkpoint_path)


async def test_cli_resume_requires_task_hash_match_and_derives_output(tmp_path: Path) -> None:
    output = tmp_path / "run"
    agent = WebAgent(
        _Planner([ToolCall(tool_name="search")]), _Browser(), _Executor(), _config(output)
    )
    agent._observe = _async_state  # type: ignore[method-assign]
    await agent.run("recover me", max_steps=1)
    checkpoint = RunLayout.from_root(output).checkpoint_path
    args = argparse.Namespace(resume=str(checkpoint), task=None, output=None, interactive=False)

    with pytest.raises(ValueError, match="requires --task"):
        _apply_resume_arguments(args)
    args.task = "recover me"
    assert _apply_resume_arguments(args) == str(checkpoint)
    assert Path(args.output) == output
    mismatch = argparse.Namespace(
        resume=str(checkpoint),
        task="recover me",
        output=str(tmp_path / "other"),
        interactive=False,
    )
    with pytest.raises(ValueError, match="run directory"):
        _apply_resume_arguments(mismatch)


async def test_strict_eval_programmatic_resume_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "run"
    first = WebAgent(
        _Planner([ToolCall(tool_name="search")]), _Browser(), _Executor(), _config(output)
    )
    first._observe = _async_state  # type: ignore[method-assign]
    await first.run("strict task", max_steps=1)
    strict_config = _config(output).model_copy(update={"strict_eval_mode": True})
    strict = WebAgent(_Planner([]), _Browser(), _Executor(), strict_config)

    with pytest.raises(ValueError, match="strict-eval"):
        await strict.run("strict task", resume_from=RunLayout.from_root(output).checkpoint_path)


async def test_real_planner_failure_switches_strategy_and_injects_plan_state(
    tmp_path: Path,
) -> None:
    planner = _Planner([])
    agent = WebAgent(planner, _Browser(), _Executor(), _config(tmp_path / "run"))
    agent._current_task = "recover"
    state = _LoopState(
        time.time(),
        planning_state=PlanningState.create("recover", ["find another route"]),
        strategy_manager=StrategyManager(),
    )

    assert await agent._think(_state(), 1, state) is None

    assert "CONTROLLER PLAN STATE" in planner.histories[0]
    assert "CONTROLLER STRATEGY HINT" in planner.histories[0]
    assert state.strategy_manager.state.current == "recovery"
    assert any(event["type"] == "strategy_switch" for event in agent._runtime_events)
    assert any(event["type"] == "replan" for event in agent._runtime_events)


def test_cli_parser_accepts_resume_without_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["webagent", "--resume", "/tmp/checkpoint.json"])
    args = parse_args()
    assert args.resume == "/tmp/checkpoint.json"
    assert args.task is None


def test_checkpoint_filename_cannot_escape_checkpoint_directory() -> None:
    with pytest.raises(ValueError, match="plain filename"):
        AgentConfig(_env_file=None, checkpoint_filename="../checkpoint.json")


def test_checkpoint_tab_coordinates_drop_secrets_and_local_paths() -> None:
    assert _checkpoint_tab_url("file:///Users/me/private.pdf") == "about:blank"
    assert (
        _checkpoint_tab_url("https://user:pw@example.test/search?q=qwen&token=secret#result")
        == "https://example.test/search"
    )


def test_checkpoint_history_omits_dom_input_paths_reasoning_and_url_tokens(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    local_artifact = output / "artifacts" / "report.pdf"
    local_artifact.parent.mkdir(parents=True)
    local_artifact.write_bytes(b"pdf")
    step = AgentStep(
        step_number=1,
        timestamp="now",
        browser_state=BrowserState(
            screenshot=None,
            dom_summary="password value: swordfish",
            url="https://user:pw@example.test/callback?q=ok&id_token=token-secret",
            title="Private",
            timestamp="now",
        ),
        tool_call=ToolCall(
            tool_name="type",
            parameters={
                "selector": {"type": "css", "value": "#password"},
                "text": "swordfish",
                "path": "/Users/private/credentials.txt",
            },
            reasoning="The password is swordfish",
        ),
        tool_result=ToolResult(
            success=True,
            tool_name="type",
            data={
                "path": str(local_artifact),
                "source_url": "https://example.test/result?token=result-secret&q=ok",
                "content": "private form contents",
            },
        ),
        duration_seconds=0.1,
    )

    saved = _checkpoint_step(step, output)
    encoded = json.dumps(saved)

    for secret in (
        "swordfish",
        "token-secret",
        "result-secret",
        "/Users/private",
        "private form contents",
    ):
        assert secret not in encoded
    assert saved["browser_state"]["dom_summary"].startswith("(omitted")
    assert saved["browser_state"]["url"] == "https://example.test/callback"
    assert saved["tool_call"]["parameters"]["text"] == "[redacted]"
    assert saved["tool_call"]["parameters"]["path"] == "[redacted]"
    assert saved["tool_call"]["reasoning"] == ""
    assert saved["tool_result"]["data"]["path"] == "artifacts/report.pdf"


def test_inspect_download_links_evidence_counts_as_progress() -> None:
    result = ToolResult(
        success=True,
        tool_name="inspect_download_links",
        data={
            "candidates": [{"url": "https://example.test/report.pdf"}],
            "date_evidence": [{"datetime": "2026-08-26T12:29:38Z"}],
        },
    )

    assert _made_progress(result, _state(), _state()) is True
    assert (
        _made_progress(
            ToolResult(success=True, tool_name="wait", data={"waited_seconds": 1}),
            _state(),
            _state(),
        )
        is False
    )


def test_nested_risk_denial_is_a_strategy_policy_denial() -> None:
    assert _policy_was_denied({"risk": {"decision": "deny"}}) is True
    assert _policy_was_denied({"decision": "deny"}) is True
    assert _policy_was_denied({"risk": {"decision": "allow"}}) is False


def test_nested_risk_denial_switches_the_live_strategy(tmp_path: Path) -> None:
    agent = WebAgent(_Planner([]), _Browser(), _Executor(), _config(tmp_path / "run"))
    state = _LoopState(
        time.time(),
        planning_state=PlanningState.create("safe action", ["act"]),
        strategy_manager=StrategyManager(),
    )
    denied = ToolResult(
        success=False,
        tool_name="click",
        error="Risk policy denied tool call",
        audit={"risk": {"decision": "deny"}},
    )

    agent._observe_strategy_result(
        state,
        step_count=1,
        tool_call=ToolCall(tool_name="click"),
        tool_result=denied,
        before=_state(),
        after=_state(),
    )

    assert state.strategy_manager.state.current == "search-discovery"
    assert any(event["type"] == "strategy_switch" for event in agent._runtime_events)


def test_planner_repair_hint_matches_transport() -> None:
    class Native:
        effective_output_mode = "native-tools"

    class Json:
        effective_output_mode = "json-schema"

    assert "provider-native function" in _planner_repair_hint(Native())  # type: ignore[arg-type]
    assert "valid JSON action" in _planner_repair_hint(Json())  # type: ignore[arg-type]


async def test_checkpoint_json_contains_no_free_text_secrets_or_absolute_paths(
    tmp_path: Path,
) -> None:
    secret = "checkpoint-swordfish-9281"
    output = tmp_path / "run"
    planner = _Planner(
        [
            ToolCall(
                tool_name="type",
                parameters={"selector": "#password", "text": secret},
                reasoning=f"enter {secret}",
            )
        ]
    )
    agent = WebAgent(planner, _Browser(), _SensitiveExecutor(secret), _config(output))

    async def sensitive_state() -> BrowserState:
        return BrowserState(
            screenshot=None,
            dom_summary=f"password={secret}",
            url=f"https://user:pw@example.test/callback?token={secret}",
            title=secret,
            timestamp="2026-08-30T00:00:00Z",
        )

    agent._observe = sensitive_state  # type: ignore[method-assign]
    await agent.run(f"log in with password {secret}", max_steps=1)
    state = agent._active_loop_state
    assert state is not None and state.planning_state is not None
    state.planning_state = state.planning_state.record_evidence(
        step_number=1,
        summary=f"private evidence {secret}",
        source=f"https://example.test/source?api_key={secret}",
    )
    state.last_figure_path = f"/Users/private/{secret}.png"
    agent._runtime_events.append(
        {"type": "test_event", "reason": secret, "url": f"https://x.test/?token={secret}"}
    )
    await agent._save_checkpoint(state, status="interrupted")

    raw = RunLayout.from_root(output).checkpoint_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "user:pw" not in raw
    assert "/Users/private" not in raw
    assert '"task"' not in raw
