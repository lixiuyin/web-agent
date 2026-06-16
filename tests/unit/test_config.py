"""Tests for AgentConfig."""

from webagent.core.config import AgentConfig


def test_default_config(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    monkeypatch.delenv("AGENT_MODEL_API_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL_API_KEY", raising=False)
    cfg = AgentConfig(_env_file=None)
    assert cfg.model_name == "qwen-vl-plus"
    assert cfg.max_steps == 100  # Updated default
    assert cfg.use_vllm is False
    assert cfg.viewport_width == 1280


def test_config_override():
    cfg = AgentConfig(_env_file=None, max_steps=10, model_name="test-model")
    assert cfg.max_steps == 10
    assert cfg.model_name == "test-model"


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "99")
    monkeypatch.setenv("AGENT_MODEL_NAME", "env-model")
    cfg = AgentConfig(_env_file=None)
    assert cfg.max_steps == 99
    assert cfg.model_name == "env-model"
