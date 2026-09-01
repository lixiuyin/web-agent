"""Centralised configuration via pydantic-settings.

Values can be provided through environment variables (prefixed ``AGENT_``),
a ``.env`` file, or programmatically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# Anchor the .env lookup to the project root (…/web-agent/.env) so config loads
# regardless of the current working directory.  Falls back to a CWD-relative
# ".env" when run from an installed layout where the source tree isn't present.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_FILE_PATH = str(_ENV_FILE) if _ENV_FILE.exists() else ".env"


class AgentConfig(BaseSettings):
    """All runtime settings for the web agent."""

    model_config = {
        "env_prefix": "AGENT_",
        "env_file": _ENV_FILE_PATH,
        "extra": "ignore",
        "populate_by_name": True,
    }

    # Model / API
    model_name: str = Field(default="qwen-vl-plus", description="Model identifier")
    model_api_url: str | None = Field(default=None, description="Remote API endpoint")
    model_api_key: str | None = Field(default=None, description="Remote API key")
    endpoint_access_mode: Literal["unknown", "shared", "byok"] = Field(
        default="unknown",
        description=(
            "Non-secret declaration of the provider credential path used for an experiment"
        ),
    )
    github_token: str = Field(
        default="",
        description="Optional GitHub token for higher-rate public repository metadata searches",
    )
    official_report_source_timeout_seconds: float = Field(
        default=15.0,
        ge=0.1,
        le=120.0,
        description="Hard timeout for each source queried by official_report_search",
    )
    hybrid_official_report_max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum official_report_search attempts for one normalized subject family",
    )
    hybrid_evidence_repeat_limit: int = Field(
        default=3,
        ge=2,
        le=5,
        description=(
            "Consecutive unchanged Hybrid evidence gaps before bounded corroboration ends"
        ),
    )
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
    api_transient_retries: int = Field(
        default=2,
        ge=0,
        le=8,
        description="Retries for planner HTTP 429 and 5xx responses after the initial request",
    )
    api_retry_base_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=30.0,
        description="Initial exponential-backoff delay for transient planner HTTP responses",
    )
    api_retry_max_seconds: float = Field(
        default=10.0,
        ge=0.0,
        le=120.0,
        description="Maximum Retry-After or exponential-backoff delay per planner retry",
    )
    planner_max_tokens: int = Field(
        default=4096,
        ge=512,
        le=16000,
        description="Maximum completion tokens for planner tool-call responses",
    )
    planner_reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = Field(
        default=None,
        description=(
            "Optional OpenAI/OpenRouter reasoning effort for planner calls; omitted by "
            "default for provider compatibility"
        ),
    )
    planner_screenshot_mode: Literal["auto", "always", "never"] = Field(
        default="auto",
        description=(
            "Whether planner calls include browser screenshots. auto sends them for sparse or "
            "explicitly visual states while keeping text-rich pages DOM-only."
        ),
    )
    vision_max_tokens: int = Field(
        default=2000,
        ge=512,
        le=16000,
        description="Maximum completion tokens for detailed chat-based image analysis",
    )
    vision_brief_max_tokens: int = Field(
        default=1200,
        ge=256,
        le=8000,
        description="Maximum completion tokens for short chat-based image analysis",
    )
    vision_max_words: int = Field(
        default=350,
        ge=100,
        le=2000,
        description="Requested word limit for detailed image-analysis answers",
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
    browser_slow_mo_ms: int = Field(
        default=0,
        ge=0,
        description="Fixed Playwright delay after each browser operation (milliseconds)",
    )
    browser_humanize_delays: bool = Field(
        default=False,
        description="Explicitly add randomized browser delays (off by default)",
    )
    browser_ignore_https_errors: bool = Field(
        default=False,
        description="Explicitly accept invalid HTTPS certificates (unsafe; off by default)",
    )
    stealth_mode: bool = Field(
        default=False,
        description="Explicitly enable enhanced anti-detection compatibility mode",
    )
    allow_google_search: bool = Field(
        default=False,
        description=(
            "Allow automated Google searches. Disabled by default because repeated "
            "Playwright searches commonly trigger Google's human-verification page; "
            "other configured search engines remain available."
        ),
    )
    search_default_engine: Literal[
        "bing", "seznam", "yahoo_japan", "yahoo", "duckduckgo", "google"
    ] = Field(
        default="bing",
        description=(
            "Search engine used when the planner omits the engine parameter; Google still "
            "requires browser opt-in or configured API credentials"
        ),
    )
    google_search_api_key: str = Field(
        default="",
        repr=False,
        description=(
            "Optional Google Custom Search JSON API key for an existing Google customer; "
            "never written to checkpoints or configuration fingerprints"
        ),
    )
    google_search_engine_id: str = Field(
        default="",
        description="Optional Programmable Search Engine identifier paired with the API key",
    )
    google_search_api_timeout_seconds: float = Field(
        default=15.0,
        ge=0.1,
        le=120.0,
        description="Hard timeout for Google Custom Search JSON API requests",
    )
    search_bing_market: str | None = Field(
        default="en-US",
        pattern=r"^[a-z]{2}-[A-Z]{2}$",
        description=("Bing market such as en-US; None preserves Bing's regional default"),
    )
    browser_locale: str | None = Field(
        default=None,
        description="Explicit locale override; None preserves the browser/system default",
    )
    browser_timezone_id: str | None = Field(
        default=None,
        description="Explicit IANA timezone override; None preserves the browser/system default",
    )
    browser_proxy_server: str = Field(
        default="",
        repr=False,
        description=(
            "Optional explicit browser proxy URL; empty keeps the browser's direct route. "
            "Embedded credentials are rejected"
        ),
    )
    search_engine_cooldown_seconds: float = Field(
        default=300.0,
        ge=0.0,
        le=86400.0,
        description=(
            "Session cooldown after a search engine returns a challenge, navigation failure, "
            "or clearly irrelevant constrained results"
        ),
    )
    browser_stale_profile_max_age_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        le=604800.0,
        description=(
            "Age threshold for removing marked temporary profiles whose owner process is gone"
        ),
    )

    # Agent loop
    max_steps: int = 100
    task_timeout: int = 1200
    tool_timeout: int = Field(default=600, description="Max seconds any single tool call may run")
    max_consecutive_failures: int = 5
    post_action_wait_ms: int = Field(default=500, ge=0)
    observation_stability_timeout_ms: int = Field(
        default=3000,
        ge=0,
        le=30000,
        description=(
            "Maximum time to wait for URL, readyState, and DOM-size signals to stabilize before "
            "capturing an observation"
        ),
    )
    observation_stable_ms: int = Field(
        default=400,
        ge=0,
        le=5000,
        description="Continuous DOM-stability window required before an observation is captured",
    )
    history_context_length: int = 10
    history_full_result_steps: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Number of newest history steps retaining full tool-result evidence; older steps "
            "are summarized to prevent planner context exhaustion"
        ),
    )
    planner_max_attempts: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Planner attempts per logical step; retries malformed/empty responses once",
    )
    elicit_terminal_confidence: bool = Field(
        default=False,
        description=(
            "Ask the planner for a task-success probability after terminal execution but before "
            "the external benchmark judge runs"
        ),
    )
    confidence_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=120.0,
        description="Wall-clock cap for terminal task-success confidence elicitation",
    )

    # Captcha handling
    captcha_pause: bool = Field(default=True, description="Detect and report captcha challenges")
    captcha_handling: str = Field(
        default="report",
        description=(
            "Challenge response: report and wait in headed mode, fail immediately, or "
            "explicitly wait_for_human; headless runs always fail closed"
        ),
    )
    captcha_wait_timeout_seconds: float = Field(
        default=180.0,
        ge=1.0,
        le=3600.0,
        description="Maximum headed-browser wait for a human to clear a challenge",
    )
    captcha_poll_interval_seconds: float = Field(
        default=2.0,
        ge=0.25,
        le=30.0,
        description="Challenge recheck interval while waiting for human intervention",
    )

    # ── Document parser cascade (cloud OCR: Marker → MinerU → Paddle) ──────
    # Soft routing hint (not a hard override): marker | mineru | paddle
    ocr_provider: str = Field(default="marker", description="Preferred parser, promoted to primary")

    marker_base_url: str = Field(
        default="https://www.datalab.to/api/v1/convert",
        description="Datalab document-conversion submission endpoint",
    )
    marker_api_key: str = Field(default="", description="datalab.to Marker API key")
    marker_max_wait_seconds: int = Field(default=300)

    mineru_base_url: str = Field(
        default="https://mineru.net/api/v4",
        description="MinerU Precision Extract API v4 root",
    )
    mineru_api_key: str = Field(default="", description="mineru.net v4 API key (JWT)")
    mineru_max_wait_seconds: int = Field(default=600)

    paddleocr_base_url: str = Field(
        default="https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
        description="PaddleOCR asynchronous Jobs API endpoint",
    )
    paddleocr_api_key: str = Field(default="")
    paddleocr_model: str = Field(default="PP-StructureV3", description="PaddleOCR cloud model")
    paddleocr_max_wait_seconds: int = Field(default=600)

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
    local_figure_fast_path: bool = Field(
        default=True,
        description=(
            "Render unambiguous caption-grounded vector/raster figures locally before cloud parsing"
        ),
    )
    local_figure_min_confidence: float = Field(
        default=0.9,
        ge=0.5,
        le=1.0,
        description="Minimum detector confidence required to bypass structured cloud parsing",
    )
    local_figure_render_dpi: int = Field(
        default=144,
        ge=72,
        le=300,
        description="Resolution for locally rendered PDF figure crops",
    )

    # Loop detection
    enable_loop_detection: bool = Field(default=True, description="Enable action loop detection")
    loop_window_size: int = Field(default=10, description="Number of recent actions to track")
    loop_threshold: int = Field(default=5, description="Repetitions before declaring a loop")

    # Structured planning
    planner_output_mode: Literal["auto", "native-tools", "json-schema", "prompt-json"] = Field(
        default="auto",
        description=(
            "Planner action transport: auto prefers provider-native function tools and "
            "falls back through provider JSON Schema to prompt JSON only when the provider "
            "explicitly reports a structured-output feature as unsupported"
        ),
    )
    use_structured_output: bool = Field(
        default=False,
        description=(
            "Deprecated compatibility switch; planner_output_mode is authoritative when set"
        ),
    )

    # Recovery and controller-level replanning
    checkpoint_enabled: bool = Field(
        default=True,
        description="Persist an atomic, non-secret controller checkpoint after every step",
    )
    checkpoint_filename: str = Field(
        default="latest.json",
        min_length=1,
        description=(
            "Checkpoint filename written below output_dir/control/checkpoints; the default "
            "layout uses latest.json"
        ),
    )
    strategy_enabled: bool = Field(
        default=True,
        description="Enable deterministic strategy switching from observable runtime signals",
    )
    strategy_failure_threshold: int = Field(default=2, ge=1)
    strategy_no_progress_threshold: int = Field(default=3, ge=1)
    strategy_max_switches: int = Field(default=6, ge=1)

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
        description=(
            "Exact run directory for the Python API. When the CLI has no explicit --output, "
            "this value (including AGENT_OUTPUT_DIR) is treated as a workspace root and a "
            "unique runs/YYYY-MM-DD/model/task-id child is allocated."
        ),
    )
    strict_eval_mode: bool = Field(
        default=False,
        description=(
            "Run an isolated search-engine-only evaluation: use an ephemeral browser profile, "
            "disable direct-source shortcuts and persistent PDF cache, and emit an auditable trace"
        ),
    )
    search_engine_only: bool = Field(
        default=False,
        description=(
            "Require browser search as the first action and reject unobserved goto/download URLs"
        ),
    )
    discovery_mode: Literal["browser-grounded", "hybrid"] = Field(
        default="hybrid",
        description=(
            "Tool exposure profile: hybrid uses browser and direct first-party discovery for "
            "ordinary performance; browser-grounded hides direct APIs for controlled evaluation"
        ),
    )
    high_risk_action_policy: Literal["deny", "prompt", "allow"] = Field(
        default="deny",
        description=(
            "Authorization for externally consequential actions: deny, prompt for human "
            "confirmation, or explicitly allow"
        ),
    )
    persistent_pdf_cache: bool = Field(
        default=False,
        description="Explicitly reuse successful cloud PDF parses across runs by content hash",
    )
    pdf_cache_dir: Path = Field(
        default=Path("~/.cache/webagent/pdf"),
        description="Content-addressed cache for successful cloud PDF parses",
    )
    browser_profile_mode: str = Field(
        default="temporary",
        description="Browser profile mode: persistent or temporary",
    )
    browser_channel: Literal["bundled", "chrome"] = Field(
        default="bundled",
        description=(
            "Browser binary: Playwright's reproducible bundled Chromium or the locally "
            "installed stable Google Chrome channel"
        ),
    )
    browser_profile_dir: Path = Field(
        default=Path("./browser_profile"),
        description="Persistent Chromium profile directory",
    )
    browser_upload_root: Path = Field(
        default=Path("./uploads"),
        description="Only files below this directory may be disclosed through upload_file",
    )

    @field_validator("output_dir", mode="before")
    @classmethod
    def _resolve_output_dir(cls, v: Path | str) -> Path:
        """Resolve output directory to absolute path, expanding ~ and $VAR."""
        s = str(v)
        s = os.path.expandvars(s)  # expand $HOME, $AGENT_OUTPUT_DIR, etc.
        return Path(s).expanduser().resolve()

    @field_validator("browser_profile_dir", "browser_upload_root", mode="before")
    @classmethod
    def _resolve_browser_profile_dir(cls, v: Path | str) -> Path:
        return Path(os.path.expandvars(str(v))).expanduser().resolve()

    @field_validator("pdf_cache_dir", mode="before")
    @classmethod
    def _resolve_pdf_cache_dir(cls, v: Path | str) -> Path:
        return Path(os.path.expandvars(str(v))).expanduser().resolve()

    @field_validator("browser_profile_mode")
    @classmethod
    def _validate_browser_profile_mode(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in {"persistent", "temporary"}:
            raise ValueError("browser_profile_mode must be 'persistent' or 'temporary'")
        return normalized

    @field_validator("checkpoint_filename")
    @classmethod
    def _validate_checkpoint_filename(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("checkpoint_filename must be a plain filename")
        return value

    @field_validator("captcha_handling")
    @classmethod
    def _validate_captcha_handling(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in {"report", "fail", "wait_for_human"}:
            raise ValueError("captcha_handling must be report, fail, or wait_for_human")
        return normalized

    @field_validator("browser_proxy_server")
    @classmethod
    def _validate_browser_proxy_server(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https", "socks4", "socks5"} or not parsed.hostname:
            raise ValueError("browser_proxy_server must be an http, https, socks4, or socks5 URL")
        if parsed.username or parsed.password:
            raise ValueError("browser_proxy_server must not contain embedded credentials")
        return normalized

    @model_validator(mode="after")
    def _validate_google_search_api_credentials(self) -> AgentConfig:
        """Require the Google API key and engine ID as one atomic configuration."""
        has_key = bool(self.google_search_api_key.strip())
        has_engine_id = bool(self.google_search_engine_id.strip())
        if has_key != has_engine_id:
            raise ValueError(
                "google_search_api_key and google_search_engine_id must be configured together"
            )
        return self

    @model_validator(mode="after")
    def _enforce_evaluation_isolation(self) -> AgentConfig:
        """Make every strict/search evaluation non-persistent and shortcut-free."""
        if self.strict_eval_mode or self.search_engine_only:
            self.strict_eval_mode = True
            self.search_engine_only = True
            # Strict evaluation is deliberately a single uninterrupted trace.
            # It cannot resume, so persisting recovery checkpoints would create
            # unusable state and falsely imply that continuation is supported.
            self.checkpoint_enabled = False
            self.discovery_mode = "browser-grounded"
            self.high_risk_action_policy = "deny"
            self.browser_ignore_https_errors = False
            self.stealth_mode = False
            self.persistent_pdf_cache = False
            self.browser_profile_mode = "temporary"
            self.browser_channel = "bundled"
            self.search_default_engine = "bing"
            if self.captcha_handling == "report":
                self.captcha_handling = "fail"

        if self.browser_channel == "chrome" and self.stealth_mode:
            raise ValueError(
                "browser_channel=chrome must use native browser properties; disable stealth_mode"
            )

        if self.browser_profile_mode == "persistent":
            profile_dir = self.browser_profile_dir.resolve()
            home = Path.home().resolve()
            default_profile_roots = (
                home / "Library/Application Support/Google/Chrome",
                home / ".config/google-chrome",
                Path(os.environ.get("LOCALAPPDATA", "__unset_local_app_data__"))
                / "Google/Chrome/User Data",
            )
            for root in default_profile_roots:
                root = root.resolve()
                if profile_dir == root or root in profile_dir.parents:
                    raise ValueError(
                        "browser_profile_dir must be a dedicated automation profile, not the "
                        "daily Chrome user-data directory"
                    )
        return self

    @property
    def artifacts_dir(self) -> Path:
        """Return the tool-artifact containment root for this exact run."""
        return self.output_dir / "artifacts"
