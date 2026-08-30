"""Tests for AgentConfig."""

import pytest
from pydantic import ValidationError

from webagent.core.config import AgentConfig


def test_default_planner_output_prefers_provider_native_structured_actions() -> None:
    assert AgentConfig(_env_file=None).planner_output_mode == "auto"


def test_default_config(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    monkeypatch.delenv("AGENT_MODEL_API_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL_API_KEY", raising=False)
    cfg = AgentConfig(_env_file=None)
    assert cfg.model_name == "qwen-vl-plus"
    assert cfg.max_steps == 100  # Updated default
    assert cfg.use_vllm is False
    assert cfg.allow_google_search is False
    assert cfg.github_token == ""
    assert cfg.official_report_source_timeout_seconds == 15.0
    assert cfg.planner_max_tokens == 4096
    assert cfg.planner_reasoning_effort is None
    assert cfg.vision_max_tokens == 2000
    assert cfg.vision_brief_max_tokens == 1200
    assert cfg.vision_max_words == 350
    assert cfg.planner_max_attempts == 2
    assert cfg.history_context_length == 10
    assert cfg.history_full_result_steps == 2
    assert cfg.browser_profile_mode == "temporary"
    assert cfg.persistent_pdf_cache is False
    assert cfg.search_engine_only is False
    assert cfg.discovery_mode == "browser-grounded"
    assert cfg.high_risk_action_policy == "deny"
    assert cfg.browser_upload_root.name == "uploads"
    assert cfg.captcha_handling == "report"
    assert cfg.viewport_width == 1280
    assert cfg.browser_slow_mo_ms == 0
    assert cfg.browser_humanize_delays is False
    assert cfg.browser_locale is None
    assert cfg.browser_timezone_id is None
    assert cfg.browser_stale_profile_max_age_seconds == 3600.0
    assert cfg.browser_ignore_https_errors is False
    assert cfg.stealth_mode is False
    assert cfg.checkpoint_enabled is True
    assert cfg.checkpoint_filename == "latest.json"
    assert cfg.marker_base_url == "https://www.datalab.to/api/v1/convert"
    assert cfg.mineru_base_url == "https://mineru.net/api/v4"
    assert cfg.paddleocr_base_url == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    assert cfg.paddleocr_model == "PP-StructureV3"
    assert cfg.local_figure_fast_path is True
    assert cfg.local_figure_min_confidence == 0.9
    assert cfg.local_figure_render_dpi == 144


def test_config_override():
    cfg = AgentConfig(
        _env_file=None,
        max_steps=10,
        model_name="test-model",
        browser_headless=False,
    )
    assert cfg.max_steps == 10
    assert cfg.model_name == "test-model"
    assert cfg.browser_headless is False


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "99")
    monkeypatch.setenv("AGENT_MODEL_NAME", "env-model")
    cfg = AgentConfig(_env_file=None)
    assert cfg.max_steps == 99
    assert cfg.model_name == "env-model"


def test_search_config_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_GOOGLE_SEARCH", "true")
    monkeypatch.setenv("AGENT_GITHUB_TOKEN", "test-token")
    cfg = AgentConfig(_env_file=None)
    assert cfg.allow_google_search is True
    assert cfg.github_token == "test-token"


def test_search_engine_only_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_ENGINE_ONLY", "true")
    assert AgentConfig(_env_file=None).search_engine_only is True


def test_hybrid_discovery_mode_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISCOVERY_MODE", "hybrid")
    assert AgentConfig(_env_file=None).discovery_mode == "hybrid"


def test_post_action_wait_must_be_non_negative():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, post_action_wait_ms=-1)


def test_browser_profile_mode_validation():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, browser_profile_mode="shared-ish")


def test_discovery_mode_validation():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, discovery_mode="api-only")


def test_high_risk_action_policy_validation():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, high_risk_action_policy="silent")


def test_strict_eval_fails_closed_on_captcha_by_default():
    cfg = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        discovery_mode="hybrid",
        browser_ignore_https_errors=True,
        stealth_mode=True,
    )
    assert cfg.captcha_handling == "fail"
    assert cfg.search_engine_only is True
    assert cfg.discovery_mode == "browser-grounded"
    assert cfg.high_risk_action_policy == "deny"
    assert cfg.browser_ignore_https_errors is False
    assert cfg.stealth_mode is False
    assert cfg.checkpoint_enabled is False


def test_captcha_handling_validation_and_human_wait_override():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, captcha_handling="solve-automatically")
    cfg = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        captcha_handling="wait_for_human",
    )
    assert cfg.captcha_handling == "wait_for_human"


def test_model_output_budgets_are_bounded():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, planner_max_tokens=100)
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, vision_max_words=50)
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, planner_reasoning_effort="unbounded")


def test_local_figure_fast_path_settings_are_bounded():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, local_figure_min_confidence=0.4)
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, local_figure_render_dpi=600)
