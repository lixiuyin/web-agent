"""Tests for CLI compatibility and planner selection."""

from __future__ import annotations

import subprocess
import sys

from webagent.cli import _build_planner, parse_args
from webagent.core.config import AgentConfig
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
    )

    planner = _build_planner(cfg)

    assert isinstance(planner, APIPlanner)
    assert planner.api_url == "https://api.example.test/v1/chat/completions"
    assert planner.model_name == "remote-model"


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

    assert "0.1.0" in result.stdout
