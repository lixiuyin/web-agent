"""Tests for AgentConfig."""

from pathlib import Path

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
    assert cfg.endpoint_access_mode == "unknown"
    assert cfg.max_steps == 100  # Updated default
    assert cfg.use_vllm is False
    assert cfg.allow_google_search is False
    assert cfg.search_default_engine == "bing"
    assert cfg.google_search_api_key == ""
    assert cfg.google_search_engine_id == ""
    assert cfg.google_search_api_timeout_seconds == 15.0
    assert cfg.search_bing_market == "en-US"
    assert cfg.github_token == ""
    assert cfg.official_report_source_timeout_seconds == 15.0
    assert cfg.planner_max_tokens == 4096
    assert cfg.planner_reasoning_effort is None
    assert cfg.planner_screenshot_mode == "auto"
    assert cfg.vision_max_tokens == 2000
    assert cfg.vision_brief_max_tokens == 1200
    assert cfg.vision_max_words == 350
    assert cfg.planner_max_attempts == 2
    assert cfg.history_context_length == 10
    assert cfg.history_full_result_steps == 2
    assert cfg.observation_stability_timeout_ms == 3000
    assert cfg.observation_stable_ms == 400
    assert cfg.browser_profile_mode == "temporary"
    assert cfg.browser_channel == "bundled"
    assert cfg.persistent_pdf_cache is False
    assert cfg.search_engine_only is False
    assert cfg.discovery_mode == "hybrid"
    assert cfg.hybrid_official_report_max_attempts == 2
    assert cfg.hybrid_evidence_repeat_limit == 3
    assert cfg.high_risk_action_policy == "deny"
    assert cfg.browser_upload_root.name == "uploads"
    assert cfg.captcha_handling == "report"
    assert cfg.viewport_width == 1280
    assert cfg.browser_slow_mo_ms == 0
    assert cfg.browser_humanize_delays is False
    assert cfg.browser_locale is None
    assert cfg.browser_timezone_id is None
    assert cfg.browser_proxy_server == ""
    assert cfg.search_engine_cooldown_seconds == 300.0
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
    monkeypatch.setenv("AGENT_ENDPOINT_ACCESS_MODE", "byok")
    cfg = AgentConfig(_env_file=None)
    assert cfg.max_steps == 99
    assert cfg.model_name == "env-model"
    assert cfg.endpoint_access_mode == "byok"


def test_search_config_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_GOOGLE_SEARCH", "true")
    monkeypatch.setenv("AGENT_SEARCH_DEFAULT_ENGINE", "google")
    monkeypatch.setenv("AGENT_SEARCH_BING_MARKET", "en-US")
    monkeypatch.setenv("AGENT_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("AGENT_GOOGLE_SEARCH_API_KEY", "google-key")
    monkeypatch.setenv("AGENT_GOOGLE_SEARCH_ENGINE_ID", "engine-id")
    cfg = AgentConfig(_env_file=None)
    assert cfg.allow_google_search is True
    assert cfg.search_default_engine == "google"
    assert cfg.search_bing_market == "en-US"
    assert cfg.github_token == "test-token"
    assert cfg.google_search_api_key == "google-key"
    assert cfg.google_search_engine_id == "engine-id"


def test_yahoo_japan_can_be_the_default_search_engine() -> None:
    cfg = AgentConfig(_env_file=None, search_default_engine="yahoo_japan")
    assert cfg.search_default_engine == "yahoo_japan"


def test_browser_channel_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BROWSER_CHANNEL", "chrome")

    assert AgentConfig(_env_file=None).browser_channel == "chrome"


def test_browser_proxy_and_search_cooldown_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BROWSER_PROXY_SERVER", "http://127.0.0.1:7897")
    monkeypatch.setenv("AGENT_SEARCH_ENGINE_COOLDOWN_SECONDS", "45")

    cfg = AgentConfig(_env_file=None)

    assert cfg.browser_proxy_server == "http://127.0.0.1:7897"
    assert cfg.search_engine_cooldown_seconds == 45.0


@pytest.mark.parametrize(
    "value",
    ["127.0.0.1:7897", "ftp://proxy.example.test:21", "http://user:pass@proxy.example.test"],
)
def test_browser_proxy_rejects_unsafe_or_ambiguous_values(value: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, browser_proxy_server=value)


def test_google_search_api_credentials_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        AgentConfig(_env_file=None, google_search_api_key="google-key")


def test_search_bing_market_rejects_noncanonical_value() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, search_bing_market="EN_us")


def test_search_engine_only_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_ENGINE_ONLY", "true")
    assert AgentConfig(_env_file=None).search_engine_only is True


def test_hybrid_discovery_mode_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISCOVERY_MODE", "hybrid")
    assert AgentConfig(_env_file=None).discovery_mode == "hybrid"


def test_hybrid_evidence_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, hybrid_official_report_max_attempts=0)
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, hybrid_evidence_repeat_limit=1)


def test_post_action_wait_must_be_non_negative():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, post_action_wait_ms=-1)


def test_observation_stability_bounds_are_validated():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, observation_stability_timeout_ms=-1)
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, observation_stable_ms=5001)


def test_browser_profile_mode_validation():
    with pytest.raises(ValidationError):
        AgentConfig(_env_file=None, browser_profile_mode="shared-ish")


def test_local_chrome_requires_native_browser_properties() -> None:
    with pytest.raises(ValidationError, match="disable stealth_mode"):
        AgentConfig(_env_file=None, browser_channel="chrome", stealth_mode=True)


def test_persistent_profile_rejects_daily_chrome_directory() -> None:
    daily_profile = Path.home() / "Library/Application Support/Google/Chrome/Default"
    with pytest.raises(ValidationError, match="dedicated automation profile"):
        AgentConfig(
            _env_file=None,
            browser_profile_mode="persistent",
            browser_profile_dir=daily_profile,
        )


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
    assert cfg.search_default_engine == "bing"


def test_strict_eval_forces_bing_even_when_another_default_was_requested():
    cfg = AgentConfig(
        _env_file=None,
        strict_eval_mode=True,
        search_default_engine="duckduckgo",
    )

    assert cfg.search_default_engine == "bing"


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
