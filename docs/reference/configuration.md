# Configuration reference

Runtime configuration is centralized in `src/webagent/core/config.py` through
`pydantic-settings`. Environment variables use the `AGENT_` prefix and can be loaded
from `.env`; command-line arguments override the corresponding runtime values.

This page explains stable user-facing groups. `.env.example` remains the complete
copyable template, and `webagent --help` remains the command-line authority.

## Planner and endpoint

| Setting | Default | Purpose |
|---|---|---|
| `model_api_url` | unset | OpenAI-compatible chat-completions endpoint |
| `model_api_key` | unset | Planner credential; never commit it |
| `model_name` | unset | Provider model identifier |
| `api_timeout` | `60` | Per-read planner HTTP timeout in seconds |
| `api_hard_timeout` | `300` | Hard wall-clock cap for one planner call |
| `api_transient_retries` | `2` | Bounded retries for HTTP 429 and transient 5xx responses |
| `api_retry_base_seconds` | `0.5` | Initial transient retry backoff |
| `api_retry_max_seconds` | `10` | Maximum transient retry backoff |
| `planner_max_tokens` | `4096` | Planner output budget |
| `vision_max_tokens` | `2000` | Detailed figure-analysis budget |
| `planner_reasoning_effort` | unset | Optional provider reasoning level from `none` to `max` |
| `planner_max_attempts` | `2` | Repair attempts for empty or malformed output |
| `planner_output_mode` | `auto` | Native tools first, then supported structured fallbacks |
| `planner_screenshot_mode` | `auto` | Send screenshots for sparse/visual states; alternatives are `always` and `never` |
| `vision_brief_max_tokens` | `1200` | Probe/brief vision response cap |
| `vision_max_words` | `350` | Requested concise vision-output bound |
| `history_context_length` | `10` | Number of recent actions retained in planner history |
| `history_full_result_steps` | `2` | Newest tool results replayed without summarization |
| `use_vllm` | `False` | Use a local OpenAI-compatible vLLM server |
| `vllm_api_url` | project default | Local vLLM endpoint |

If no usable planner is configured, the runtime selects `StubPlanner`. That path is for
lifecycle calibration and explicit failure reporting, not autonomous task completion.

## Agent control

| Setting | Default | Purpose |
|---|---|---|
| `max_steps` | `100` | Maximum loop iterations |
| `task_timeout` | `1200` | Whole-task wall-clock cap in seconds |
| `tool_timeout` | `600` | Per-tool wall-clock cap |
| `checkpoint_enabled` | `True` | Atomic ordinary-run recovery state |
| `checkpoint_filename` | `latest.json` | Checkpoint basename below `control/checkpoints/` |
| `strategy_enabled` | `True` | Permit bounded switch/replan responses to failures and loops |
| `enable_loop_detection` | `True` | Enable repeated-action, stagnation, oscillation, and churn signals |
| `high_risk_action_policy` | `deny` | `deny`, terminal `prompt`, or explicit `allow` |
| `output_dir` | `./outputs` | Workspace used when `--output` is omitted |

An explicit `--output` names one exact run root, not the workspace that contains multiple
runs. See [run artifacts](run-artifacts.md).

## Browser and observation

| Setting | Default | Purpose |
|---|---|---|
| `post_action_wait_ms` | `500` | Minimum delay before the post-action observation |
| `observation_stability_timeout_ms` | `3000` | Maximum bounded page-stability wait |
| `observation_stable_ms` | `400` | Required stable interval |
| `use_cdp` | `True` | CDP-enhanced element detection with fallback |
| `stealth_mode` | `False` | Compatibility opt-in; forced off in strict evaluation |
| `browser_slow_mo_ms` | `0` | Fixed operation delay |
| `browser_humanize_delays` | `False` | Explicit randomized delay compatibility option |
| `browser_locale` | unset | Preserve native locale unless set |
| `browser_timezone_id` | unset | Preserve native timezone unless set |
| `browser_proxy_server` | unset | Browser-only proxy URL without embedded credentials |
| `browser_ignore_https_errors` | `False` | Validate TLS unless explicitly overridden |
| `browser_profile_mode` | `temporary` | Isolated profile; persistent state is opt-in |
| `browser_channel` | `bundled` | Bundled Chromium; use `chrome` for trusted interactive state |
| `browser_stale_profile_max_age_seconds` | `3600` | Age floor for reaping dead-owner temporary profiles |
| `browser_upload_root` | `./uploads` | Containment root for approved uploads |
| `captcha_handling` | `report` | Report/fail or explicit headed human wait; never bypass |
| `captcha_wait_timeout_seconds` | `180` | Maximum explicit human-clearance wait |

See [browser and security](browser-and-security.md) before enabling persistent profiles,
proxies, TLS bypass, uploads, or high-risk actions.

## Search and discovery

| Setting | Default | Purpose |
|---|---|---|
| `discovery_mode` | `hybrid` | Browser plus supported direct first-party discovery |
| `search_engine_only` | `False` | Hide direct discovery and require browser search |
| `strict_eval_mode` | `False` | Isolated browser-search execution with certificate |
| `search_default_engine` | `bing` | Default browser search engine |
| `search_bing_market` | `en-US` | Deterministic Bing market |
| `search_engine_cooldown_seconds` | `300` | Cooldown after challenge, reachability, or quality failure |
| `allow_google_search` | `False` | Explicit opt-in to automated Google use |
| `google_search_api_key` | unset | Optional Google JSON API credential |
| `google_search_engine_id` | unset | Optional Google Programmable Search engine identifier |
| `google_search_api_timeout_seconds` | `15` | Optional Google API timeout |
| `github_token` | unset | Optional GitHub API rate-limit increase for Hybrid discovery |
| `official_report_source_timeout_seconds` | `15` | Independent cap for each first-party report source |
| `hybrid_official_report_max_attempts` | `2` | Per-subject direct report-search cap |
| `hybrid_evidence_repeat_limit` | `3` | Maximum unchanged Hybrid corroboration state |

Strict/search-engine-only execution overrides incompatible discovery, browser profile,
cache, and stealth settings. The full evidence contract is in
[discovery modes](../guides/discovery-modes.md).

## PDF and figure processing

| Setting | Default | Purpose |
|---|---|---|
| `ocr_provider` | `marker` | Soft routing hint for the parser cascade |
| `persistent_pdf_cache` | `False` | Cross-run parse reuse opt-in |
| `local_figure_fast_path` | `True` | Render an unambiguous exact-numbered figure locally |
| `local_figure_min_confidence` | `0.9` | Safe-bypass confidence floor |
| `local_figure_render_dpi` | `144` | Local crop resolution |
| Provider API keys | unset | Optional Marker/Datalab, MinerU, Paddle, and related services |

Provider-specific keys and endpoints are listed in `.env.example`. A configured provider
is not assumed usable until its request path passes the parser quality gate or a bounded
probe.

## Precedence and reproducibility

Configuration precedence is:

1. Explicit command-line arguments.
2. `AGENT_` environment variables.
3. Values loaded from `.env`.
4. `AgentConfig` defaults.

Benchmark manifests additionally bind relevant values into their execution contracts.
Changing a bound model, task set, source fingerprint, browser policy, step budget, or
evaluation setting starts a new comparison rather than silently extending an old one.
