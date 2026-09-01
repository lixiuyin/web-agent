"""Tests for CLI compatibility and planner selection."""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any

import pytest

from webagent import cli
from webagent.cli import (
    _apply_cli_overrides,
    _apply_evaluation_overrides,
    _build_browser,
    _build_planner,
    _print_result,
    parse_args,
)
from webagent.core.config import AgentConfig
from webagent.core.models import AgentResult
from webagent.planner.api import APIPlanner
from webagent.planner.stub import StubPlanner


def test_build_planner_uses_stub_without_backends():
    cfg = AgentConfig(
        _env_file=None,
        model_api_url=None,
        model_api_key=None,
        use_vllm=False,
    )

    planner = _build_planner(cfg)

    assert isinstance(planner, StubPlanner)


def test_build_planner_prefers_remote_api_credentials():
    cfg = AgentConfig(
        _env_file=None,
        model_api_url="https://api.example.test/v1/chat/completions",
        model_api_key="secret",
        model_name="remote-model",
        use_vllm=True,
        vllm_api_url="http://127.0.0.1:8000/v1/chat/completions",
        vllm_model_name="local-model",
        planner_max_tokens=3000,
        planner_reasoning_effort="low",
        planner_output_mode="json-schema",
        vision_max_tokens=1800,
        vision_brief_max_tokens=700,
        vision_max_words=350,
    )

    planner = _build_planner(cfg)

    assert isinstance(planner, APIPlanner)
    assert planner.api_url == "https://api.example.test/v1/chat/completions"
    assert planner.model_name == "remote-model"
    assert planner.max_tokens == 3000
    assert planner.reasoning_effort == "low"
    assert planner.output_mode == "json-schema"
    assert planner.vision_max_tokens == 1800
    assert planner.vision_brief_max_tokens == 700
    assert planner.vision_max_words == 350


def test_build_planner_uses_local_vllm_when_enabled_without_remote_api():
    cfg = AgentConfig(
        _env_file=None,
        model_api_url=None,
        model_api_key=None,
        use_vllm=True,
        vllm_api_url="http://127.0.0.1:9999/v1/chat/completions",
        vllm_api_key="token",
        vllm_model_name="local-model",
    )

    planner = _build_planner(cfg)

    assert isinstance(planner, APIPlanner)
    assert planner.api_url == "http://127.0.0.1:9999/v1/chat/completions"
    assert planner.api_key == "token"
    assert planner.model_name == "local-model"


def test_build_browser_applies_stealth_config():
    cfg = AgentConfig(
        _env_file=None,
        stealth_mode=False,
        browser_slow_mo_ms=25,
        browser_humanize_delays=True,
        browser_ignore_https_errors=True,
        browser_locale="fr-FR",
        browser_timezone_id="Europe/Paris",
        browser_stale_profile_max_age_seconds=7200,
    )

    browser = _build_browser(cfg)

    assert browser.stealth_mode is False
    assert browser.slow_mo == 25
    assert browser.humanize_delays is True
    assert browser.ignore_https_errors is True
    assert browser.locale == "fr-FR"
    assert browser.timezone_id == "Europe/Paris"
    assert browser.proxy_server is None
    assert browser.stale_profile_max_age_seconds == 7200
    assert browser.browser_channel is None


def test_build_browser_uses_local_chrome_channel() -> None:
    cfg = AgentConfig(_env_file=None, browser_channel="chrome")

    browser = _build_browser(cfg)

    assert browser.browser_channel == "chrome"


def test_build_browser_applies_explicit_proxy_without_logging_credentials() -> None:
    cfg = AgentConfig(_env_file=None, browser_proxy_server="http://127.0.0.1:7897")

    browser = _build_browser(cfg)

    assert browser.proxy_server == "http://127.0.0.1:7897"


def test_build_browser_disables_random_delays_in_strict_eval():
    cfg = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        browser_humanize_delays=True,
    )

    browser = _build_browser(cfg)

    assert browser.slow_mo == 0
    assert browser.humanize_delays is False


def test_parse_args_supports_documented_vllm_flags(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webagent",
            "--task",
            "x",
            "--use-vllm",
            "--vllm-model-name",
            "qwen3_vl",
            "--vllm-api-url",
            "http://127.0.0.1:8000/v1/chat/completions",
        ],
    )

    args = parse_args()

    assert args.use_vllm is True
    assert args.vllm_model_name == "qwen3_vl"
    assert args.vllm_api_url == "http://127.0.0.1:8000/v1/chat/completions"


def test_legacy_main_delegates_to_package_cli():
    result = subprocess.run(
        [sys.executable, "main.py", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "0.2.0" in result.stdout


def _args(**kw: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "task": None,
        "interactive": False,
        "output": None,
        "model": None,
        "api_url": None,
        "api_key": None,
        "vllm_model_name": None,
        "vllm_api_url": None,
        "vllm_api_key": None,
        "use_vllm": None,
        "headless": False,
        "headed": False,
        "strict_eval": False,
        "search_engine_only": False,
        "discovery_mode": None,
        "high_risk_actions": None,
        "browser_profile_mode": None,
        "browser_channel": None,
        "browser_proxy_server": None,
        "captcha_handling": None,
        "captcha_wait_timeout": None,
        "planner_output_mode": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestApplyCliOverrides:
    def test_scalar_and_output_overrides(self, tmp_path):
        cfg = AgentConfig(_env_file=None)
        args = _args(model="m", api_url="u", output=str(tmp_path / "out"))
        _apply_cli_overrides(cfg, args)
        assert cfg.model_name == "m"
        assert cfg.model_api_url == "u"
        assert cfg.output_dir == (tmp_path / "out")

    def test_headed_wins_when_set(self):
        cfg = AgentConfig(_env_file=None, browser_headless=True)
        _apply_cli_overrides(cfg, _args(headed=True))
        assert cfg.browser_headless is False

    def test_headless_flag(self):
        cfg = AgentConfig(_env_file=None, browser_headless=False)
        _apply_cli_overrides(cfg, _args(headless=True))
        assert cfg.browser_headless is True

    def test_browser_channel_override(self):
        cfg = AgentConfig(_env_file=None)
        _apply_cli_overrides(cfg, _args(browser_channel="chrome"))
        assert cfg.browser_channel == "chrome"

    def test_browser_proxy_override(self):
        cfg = AgentConfig(_env_file=None)
        _apply_cli_overrides(cfg, _args(browser_proxy_server="http://127.0.0.1:7897"))
        assert cfg.browser_proxy_server == "http://127.0.0.1:7897"

    def test_use_vllm_toggle(self):
        cfg = AgentConfig(_env_file=None, use_vllm=True)
        _apply_cli_overrides(cfg, _args(use_vllm=False))
        assert cfg.use_vllm is False

    def test_browser_grounded_discovery_requires_explicit_override(self):
        cfg = AgentConfig(_env_file=None)
        assert cfg.discovery_mode == "hybrid"
        _apply_cli_overrides(cfg, _args(discovery_mode="browser-grounded"))
        assert cfg.discovery_mode == "browser-grounded"

    def test_high_risk_action_policy_requires_explicit_override(self):
        cfg = AgentConfig(_env_file=None)
        assert cfg.high_risk_action_policy == "deny"
        _apply_cli_overrides(cfg, _args(high_risk_actions="prompt"))
        assert cfg.high_risk_action_policy == "prompt"

    def test_strict_eval_isolates_profile_and_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = AgentConfig(_env_file=None)
        _apply_cli_overrides(cfg, _args(strict_eval=True))
        assert cfg.strict_eval_mode is True
        assert cfg.search_engine_only is True
        assert cfg.discovery_mode == "browser-grounded"
        assert cfg.high_risk_action_policy == "deny"
        assert cfg.persistent_pdf_cache is False
        assert cfg.browser_profile_mode == "temporary"
        assert cfg.browser_channel == "bundled"
        assert cfg.captcha_handling == "fail"
        assert cfg.checkpoint_enabled is False
        assert (tmp_path / "outputs" / "runs") in cfg.output_dir.parents
        assert cfg.output_dir.parent.parent.parent.name == "runs"

    def test_search_engine_only_enables_strict_isolation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = AgentConfig(_env_file=None)
        _apply_cli_overrides(cfg, _args(search_engine_only=True))
        assert cfg.search_engine_only is True
        assert cfg.strict_eval_mode is True
        assert cfg.persistent_pdf_cache is False
        assert cfg.browser_profile_mode == "temporary"
        assert cfg.checkpoint_enabled is False
        assert (tmp_path / "outputs" / "runs") in cfg.output_dir.parents
        assert cfg.output_dir.name.startswith("interactive-session-")

    def test_default_output_is_unique_run_below_configured_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        first = AgentConfig(_env_file=None, output_dir=workspace)
        second = AgentConfig(_env_file=None, output_dir=workspace)

        _apply_cli_overrides(first, _args(task="Collect failure evidence", model="model/a"))
        _apply_cli_overrides(second, _args(task="Collect failure evidence", model="model/a"))

        assert workspace / "runs" in first.output_dir.parents
        assert first.output_dir.parent.name == "model-a"
        assert first.output_dir.name.startswith("collect-failure-evidence-")
        assert first.output_dir != second.output_dir
        assert not workspace.exists()


def test_parse_args_supports_search_engine_only(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["webagent", "--task", "x", "--search-engine-only"])
    assert parse_args().search_engine_only is True


def test_parse_args_supports_browser_channel(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["webagent", "--task", "x", "--browser-channel", "chrome"],
    )
    assert parse_args().browser_channel == "chrome"


def test_parse_args_supports_browser_proxy(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["webagent", "--task", "x", "--browser-proxy-server", "http://127.0.0.1:7897"],
    )
    assert parse_args().browser_proxy_server == "http://127.0.0.1:7897"


def test_parse_args_supports_hybrid_discovery(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["webagent", "--task", "x", "--discovery-mode", "hybrid"],
    )
    assert parse_args().discovery_mode == "hybrid"


def test_parse_args_supports_high_risk_prompt(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["webagent", "--task", "x", "--high-risk-actions", "prompt"],
    )
    assert parse_args().high_risk_actions == "prompt"


def test_parse_args_supports_captcha_human_handoff(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webagent",
            "--task",
            "x",
            "--headed",
            "--captcha-handling",
            "wait_for_human",
            "--captcha-wait-timeout",
            "45",
        ],
    )
    args = parse_args()
    assert args.captcha_handling == "wait_for_human"
    assert args.captcha_wait_timeout == 45


def test_parse_args_supports_provider_planner_output_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["webagent", "--task", "x", "--planner-output-mode", "native-tools"],
    )

    assert parse_args().planner_output_mode == "native-tools"


def test_build_browser_uses_temporary_profile_for_strict_eval():
    cfg = AgentConfig(_env_file=None, strict_eval_mode=True)
    assert _build_browser(cfg).temporary_profile is True


def test_cli_strict_eval_forces_bing_search_default() -> None:
    cfg = AgentConfig(_env_file=None, search_default_engine="duckduckgo")

    _apply_evaluation_overrides(cfg, _args(strict_eval=True))

    assert cfg.search_default_engine == "bing"


def test_build_browser_uses_isolated_native_defaults():
    browser = _build_browser(AgentConfig(_env_file=None))

    assert browser.temporary_profile is True
    assert browser.humanize_delays is False
    assert browser.locale is None
    assert browser.timezone_id is None


class TestPrintResult:
    def test_full(self, capsys):
        result = AgentResult(
            success=True,
            status="completed",
            steps_taken=3,
            total_duration=1.234,
            final_result={"summary": "the answer"},
        )
        _print_result(result)
        out = capsys.readouterr().out
        assert "Status: completed" in out
        assert "Steps: 3" in out
        assert "the answer" in out

    def test_oneline(self, capsys):
        result = AgentResult(success=False, status="failed", steps_taken=1, total_duration=0.5)
        _print_result(result, oneline=True)
        out = capsys.readouterr().out
        assert "Status: failed | Steps: 1" in out


class _FakeBrowser:
    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.started = False
        self.closed = False

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("no chromium")
        self.started = True

    async def close(self) -> None:
        self.closed = True


class _FakePlanner:
    def __init__(self) -> None:
        self.loaded = False
        self.unloaded = False

    async def load(self) -> None:
        self.loaded = True

    async def unload(self) -> None:
        self.unloaded = True


class _FakeAgent:
    def __init__(self, **kw: Any) -> None:
        self.hooks: list[Any] = []
        self.runs: list[str] = []

    def add_hook(self, hook: Any) -> None:
        self.hooks.append(hook)

    async def run(self, task: str, reset_history: bool = True) -> AgentResult:
        self.runs.append(task)
        return AgentResult(
            success=True, status="completed", steps_taken=1, total_duration=0.1, final_result={}
        )


@pytest.fixture
def patched_cli(monkeypatch, tmp_path):
    planner = _FakePlanner()
    browser = _FakeBrowser()
    agent = _FakeAgent()
    monkeypatch.setattr(cli, "_build_planner", lambda cfg: planner)
    monkeypatch.setattr(cli, "_build_browser", lambda cfg: browser)
    monkeypatch.setattr(cli, "_build_tool_registry", lambda b, c, p: cli.ToolRegistry())
    monkeypatch.setattr(cli, "ToolExecutor", lambda registry, tool_timeout=None, **kw: object())
    monkeypatch.setattr(cli, "WebAgent", lambda **kw: agent)
    monkeypatch.setattr(cli, "LoggingHook", lambda: object())
    return planner, browser, agent


async def test_run_task_executes(patched_cli, tmp_path):
    planner, browser, agent = patched_cli
    await cli.run_task(_args(task="do it", output=str(tmp_path / "o")))
    assert agent.runs == ["do it"]
    assert planner.loaded and planner.unloaded
    assert browser.started and browser.closed


async def test_run_task_requires_task(patched_cli, tmp_path, capsys):
    await cli.run_task(_args(task=None, output=str(tmp_path / "o")))
    assert "--task required" in capsys.readouterr().out


async def test_run_task_browser_start_failure(monkeypatch, tmp_path):
    planner = _FakePlanner()
    browser = _FakeBrowser(fail_start=True)
    monkeypatch.setattr(cli, "_build_planner", lambda cfg: planner)
    monkeypatch.setattr(cli, "_build_browser", lambda cfg: browser)
    with pytest.raises(RuntimeError, match="Browser failed to start"):
        await cli.run_task(_args(task="x", output=str(tmp_path / "o")))
    assert planner.unloaded
    assert browser.closed


async def test_run_task_interactive(patched_cli, monkeypatch, tmp_path):
    _planner, _browser, agent = patched_cli
    inputs = iter(["first task", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    await cli.run_task(_args(interactive=True, output=str(tmp_path / "o")))
    assert agent.runs == ["first task"]


def test_main_invokes_run_task(monkeypatch):
    called: dict[str, Any] = {}
    monkeypatch.setattr(sys, "argv", ["webagent", "--task", "hello"])
    monkeypatch.setattr(cli, "configure_logging", lambda: called.setdefault("log", True))

    async def fake_run(args: argparse.Namespace) -> None:
        called["task"] = args.task

    monkeypatch.setattr(cli, "run_task", fake_run)
    cli.main()
    assert called["log"] and called["task"] == "hello"
