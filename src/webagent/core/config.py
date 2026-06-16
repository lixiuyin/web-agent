"""Centralised configuration via pydantic-settings.

Values can be provided through environment variables (prefixed ``AGENT_``),
a ``.env`` file, or programmatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Anchor the .env lookup to the project root (…/web-agent/.env) so config loads
# regardless of the current working directory.  Falls back to a CWD-relative
# ".env" when run from an installed layout where the source tree isn't present.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_FILE_PATH = str(_ENV_FILE) if _ENV_FILE.exists() else ".env"


class AgentConfig(BaseSettings):
    """All runtime settings for the web agent."""

    model_config = {"env_prefix": "AGENT_", "env_file": _ENV_FILE_PATH, "extra": "ignore"}

    # Model / API
    model_name: str = Field(default="qwen-vl-plus", description="Model identifier")
    device: str = Field(default="cpu", description="Device for inference")
    max_new_tokens: int = Field(default=512)
    model_api_url: str | None = Field(default=None, description="Remote API endpoint")
    model_api_key: str | None = Field(default=None, description="Remote API key")
    api_timeout: int = Field(
        default=60,
        description="Per-read/connect HTTP timeout for planner calls (seconds)",
    )
    api_hard_timeout: int = Field(
        default=300,
        description=(
            "Hard wall-clock cap for a single planner HTTP call (seconds). Bounds "
            "hung/trickling connections that evade the per-read api_timeout so one "
            "stalled request cannot consume the whole task_timeout budget."
        ),
    )
    use_vllm: bool = Field(default=False, description="Attempt local vLLM inference")
    vllm_model_name: str = Field(default="qwen3_vl", description="Key in vllm model map")
    vllm_api_url: str = Field(
        default="http://127.0.0.1:8000/v1/chat/completions",
        description="OpenAI-compatible local vLLM chat completions endpoint",
    )
    vllm_api_key: str = Field(
        default="EMPTY",
        description="Bearer token for local vLLM OpenAI-compatible server",
    )

    # Browser
    browser_headless: bool = Field(default=True, alias="AGENT_BROWSER_HEADLESS")
    viewport_width: int = 1280
    viewport_height: int = 720
    browser_timeout: int = 30000
    stealth_mode: bool = Field(
        default=True, description="Enable enhanced anti-detection stealth mode"
    )

    # Agent loop
    max_steps: int = 100
    task_timeout: int = 1200
    tool_timeout: int = Field(default=600, description="Max seconds any single tool call may run")
    max_consecutive_failures: int = 5
    post_action_wait_ms: int = 500
    history_context_length: int = 10

    # Captcha handling
    captcha_pause: bool = Field(default=True, description="Pause on captcha detection")
    captcha_timeout: int = Field(default=300, description="Max wait time for captcha (seconds)")

    # ── Document parser cascade (cloud OCR: Marker → MinerU → Paddle) ──────
    # Soft routing hint (not a hard override): marker | mineru | paddle
    ocr_provider: str = Field(default="marker", description="Preferred parser, promoted to primary")

    marker_base_url: str = Field(default="https://www.datalab.to/api/v1/marker")
    marker_api_key: str = Field(default="", description="datalab.to Marker API key")
    marker_max_wait_seconds: int = Field(default=300)

    mineru_base_url: str = Field(default="https://mineru.net/api/v4")
    mineru_api_key: str = Field(default="", description="mineru.net v4 API key (JWT)")
    mineru_max_wait_seconds: int = Field(default=600)

    paddleocr_base_url: str = Field(default="", description="PaddleOCR layout-parsing endpoint")
    paddleocr_api_key: str = Field(default="")

    parser_http_timeout_seconds: float = Field(
        default=180.0, description="Per-request HTTP timeout"
    )
    parser_poll_interval_seconds: float = Field(
        default=2.0, description="Async result poll interval"
    )
    parser_proxy: str = Field(
        default="",
        description="Proxy for cloud OCR calls (e.g. socks5://127.0.0.1:7897); "
        "empty = use system HTTP(S)_PROXY / ALL_PROXY env vars",
    )
    parse_timeout_seconds: int = Field(default=900, description="Total cascade wall-clock budget")
    max_parse_pages: int = Field(
        default=1000, description="Reject documents exceeding this page count"
    )

    # Loop detection
    enable_loop_detection: bool = Field(default=True, description="Enable action loop detection")
    loop_window_size: int = Field(default=10, description="Number of recent actions to track")
    loop_threshold: int = Field(default=5, description="Repetitions before declaring a loop")

    # Enhanced planning
    use_structured_output: bool = Field(default=False, description="Use enhanced structured output")
    max_memory_length: int = Field(default=500, description="Max memory field length")

    # Enhanced snapshot (CDP and intelligent filtering)
    use_cdp: bool = Field(
        default=True, description="Use Chrome DevTools Protocol for enhanced detection"
    )
    max_snapshot_elements: int = Field(
        default=50, description="Max interactive elements to include in snapshot"
    )
    enable_ad_filtering: bool = Field(
        default=True, description="Filter out ads and trackers from snapshots"
    )

    # Output / Storage
    output_dir: Path = Field(
        default=Path("./outputs"),
        description="Base output directory for artifacts (can be set via AGENT_OUTPUT_DIR env var)",
    )

    @field_validator("output_dir", mode="before")
    @classmethod
    def _resolve_output_dir(cls, v: Path | str) -> Path:
        """Resolve output directory to absolute path, expanding ~ and $VAR."""
        s = str(v)
        s = os.path.expandvars(s)  # expand $HOME, $AGENT_OUTPUT_DIR, etc.
        return Path(s).expanduser().resolve()

    @property
    def artifacts_dir(self) -> Path:
        """Get the artifacts directory (output_dir/artifacts)."""
        return self.output_dir / "artifacts"
