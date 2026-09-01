"""CLI entry point for the webagent package."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from webagent.agent.checkpoint import CheckpointStore
from webagent.agent.hooks import LoggingHook
from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.core.models import AgentResult, ToolCall
from webagent.core.protocols import Planner
from webagent.evaluation.artifacts import OutputWorkspace, RunLayout
from webagent.tools.executor import ToolExecutor
from webagent.tools.exposure import allowed_tools_for_discovery_mode
from webagent.tools.registry import ToolRegistry
from webagent.tools.risk import ActionRiskPolicy, BrowserRiskContext, RiskAssessment
from webagent.utils.logging import configure_logging

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Note: Default values for --output and other settings come from config
    (AgentConfig), which reads from environment variables and .env file.
    Use CLI arguments to override config defaults.
    """
    import webagent

    p = argparse.ArgumentParser(
        description="webagent - autonomous web agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Version: {webagent.__version__}\n"
        f"Config: Use environment variables (AGENT_*) or .env file",
    )
    p.add_argument("--task", type=str, help="Natural-language task to execute")
    p.add_argument(
        "--resume",
        type=str,
        help="Resume a normal-mode run from an atomic checkpoint (not allowed in strict-eval)",
    )
    p.add_argument("--interactive", action="store_true", help="Interactive mode")
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Exact run directory. When omitted, AGENT_OUTPUT_DIR or ./outputs is a workspace "
            "root and the CLI allocates runs/YYYY-MM-DD/model/task-id."
        ),
    )
    p.add_argument("--model", type=str, help="Override model name (default: from config)")
    p.add_argument("--api-url", type=str, help="Override API URL (default: from config)")
    p.add_argument("--api-key", type=str, help="Override API key (default: from config)")
    p.add_argument(
        "--planner-output-mode",
        choices=("auto", "native-tools", "json-schema", "prompt-json"),
        help=("Planner action transport (default: auto provider-native tools with safe fallback)"),
    )
    vllm_group = p.add_mutually_exclusive_group()
    vllm_group.add_argument(
        "--use-vllm",
        action="store_true",
        default=None,
        help="Use a local OpenAI-compatible vLLM server when API credentials are absent",
    )
    vllm_group.add_argument(
        "--no-vllm",
        action="store_false",
        dest="use_vllm",
        default=None,
        help="Disable local vLLM fallback and use the stub planner when no API is configured",
    )
    p.add_argument(
        "--vllm-model-name",
        type=str,
        help="Override local vLLM model name (default: from config)",
    )
    p.add_argument(
        "--vllm-api-url",
        type=str,
        help="Override local vLLM OpenAI-compatible API URL (default: from config)",
    )
    p.add_argument(
        "--vllm-api-key",
        type=str,
        help="Override local vLLM API key/token (default: from config)",
    )
    p.add_argument("--headless", action="store_true", help="Force headless")
    p.add_argument("--headed", action="store_true", help="Force headed")
    p.add_argument(
        "--strict-eval",
        action="store_true",
        help=(
            "Run an isolated, search-engine-only auditable evaluation (fresh profile/output, "
            "no direct source APIs or persistent PDF cache)"
        ),
    )
    p.add_argument(
        "--search-engine-only",
        action="store_true",
        help=(
            "Strict discovery evaluation: require browser search, disable direct source APIs, "
            "and reject guessed goto/download URLs"
        ),
    )
    p.add_argument(
        "--discovery-mode",
        choices=("browser-grounded", "hybrid"),
        help=(
            "Discovery tool exposure (default: browser-grounded; hybrid explicitly enables "
            "direct arXiv/GitHub API tools)"
        ),
    )
    p.add_argument(
        "--high-risk-actions",
        choices=("deny", "prompt", "allow"),
        help=(
            "Authorization for purchases, submissions, publishing, deletion, and similar "
            "external actions (default: deny)"
        ),
    )
    p.add_argument(
        "--browser-profile-mode",
        choices=("persistent", "temporary"),
        help="Override browser profile isolation mode",
    )
    p.add_argument(
        "--browser-channel",
        choices=("bundled", "chrome"),
        help="Use Playwright's bundled Chromium or the locally installed stable Chrome",
    )
    p.add_argument(
        "--browser-proxy-server",
        type=str,
        help=(
            "Explicit browser proxy URL without embedded credentials; unset keeps the direct route"
        ),
    )
    p.add_argument(
        "--captcha-handling",
        choices=("report", "fail", "wait_for_human"),
        help=(
            "Report and wait in headed mode (default), fail immediately, or explicitly wait "
            "for manual challenge resolution; headless runs fail closed"
        ),
    )
    p.add_argument(
        "--captcha-wait-timeout",
        type=float,
        help="Seconds to wait for manual CAPTCHA clearance in report/wait_for_human mode",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {webagent.__version__}",
        help="Show version information",
    )
    return p.parse_args()


def _build_planner(cfg: AgentConfig) -> Planner:
    """Select planner based on config."""
    from webagent.planner.factory import build_planner

    return build_planner(cfg)


def _build_tool_registry(
    browser: BrowserController, config: AgentConfig, planner: Planner
) -> ToolRegistry:
    """Create tool registry with all built-in tools."""
    import webagent.tools.builtin  # noqa: F401 – triggers @tool registration

    registry = ToolRegistry()
    registry.auto_discover(browser=browser, config=config, planner=planner)
    return registry


def _build_browser(cfg: AgentConfig) -> BrowserController:
    """Build the browser with every runtime browser setting applied."""
    return BrowserController(
        headless=cfg.browser_headless,
        viewport_width=cfg.viewport_width,
        viewport_height=cfg.viewport_height,
        default_timeout=cfg.browser_timeout,
        slow_mo=cfg.browser_slow_mo_ms,
        stealth_mode=cfg.stealth_mode,
        humanize_delays=cfg.browser_humanize_delays and not cfg.strict_eval_mode,
        ignore_https_errors=cfg.browser_ignore_https_errors,
        locale=cfg.browser_locale,
        timezone_id=cfg.browser_timezone_id,
        stale_profile_max_age_seconds=cfg.browser_stale_profile_max_age_seconds,
        user_data_dir=cfg.browser_profile_dir,
        temporary_profile=cfg.strict_eval_mode or cfg.browser_profile_mode == "temporary",
        browser_channel=None if cfg.browser_channel == "bundled" else cfg.browser_channel,
        proxy_server=cfg.browser_proxy_server or None,
    )


def _apply_scalar_overrides(cfg: AgentConfig, args: argparse.Namespace) -> None:
    overrides = [
        ("model", "model_name"),
        ("api_url", "model_api_url"),
        ("api_key", "model_api_key"),
        ("vllm_model_name", "vllm_model_name"),
        ("vllm_api_url", "vllm_api_url"),
        ("vllm_api_key", "vllm_api_key"),
        ("captcha_handling", "captcha_handling"),
        ("captcha_wait_timeout", "captcha_wait_timeout_seconds"),
        ("discovery_mode", "discovery_mode"),
        ("high_risk_actions", "high_risk_action_policy"),
        ("planner_output_mode", "planner_output_mode"),
    ]
    for arg_name, cfg_name in overrides:
        value = getattr(args, arg_name, None)
        if value:
            setattr(cfg, cfg_name, value)

    if args.use_vllm is not None:
        cfg.use_vllm = args.use_vllm


def _apply_browser_overrides(cfg: AgentConfig, args: argparse.Namespace) -> None:
    if args.headless:
        cfg.browser_headless = True
    if args.headed:
        cfg.browser_headless = False
    profile_mode = getattr(args, "browser_profile_mode", None)
    if profile_mode:
        cfg.browser_profile_mode = profile_mode
    browser_channel = getattr(args, "browser_channel", None)
    if browser_channel:
        cfg.browser_channel = browser_channel
    browser_proxy_server = getattr(args, "browser_proxy_server", None)
    if browser_proxy_server:
        cfg.browser_proxy_server = browser_proxy_server


def _apply_evaluation_overrides(cfg: AgentConfig, args: argparse.Namespace) -> None:
    if getattr(args, "search_engine_only", False) or getattr(args, "strict_eval", False):
        cfg.search_engine_only = True
    if cfg.strict_eval_mode:
        cfg.search_engine_only = True
    if cfg.search_engine_only:
        cfg.strict_eval_mode = True
        cfg.checkpoint_enabled = False
        cfg.discovery_mode = "browser-grounded"
        cfg.high_risk_action_policy = "deny"
        cfg.persistent_pdf_cache = False
        cfg.browser_profile_mode = "temporary"
        cfg.browser_channel = "bundled"
        cfg.search_default_engine = "bing"
        if cfg.captcha_handling == "report":
            cfg.captcha_handling = "fail"
    if cfg.browser_channel == "chrome" and cfg.stealth_mode:
        raise ValueError(
            "browser_channel=chrome must use native browser properties; disable stealth_mode"
        )


def _apply_cli_overrides(cfg: AgentConfig, args: argparse.Namespace) -> None:
    """Copy CLI flags onto the config (unset flags keep env/default values)."""
    _apply_scalar_overrides(cfg, args)
    _apply_browser_overrides(cfg, args)
    _apply_evaluation_overrides(cfg, args)
    # ``--output`` is an exact run directory. Without it, the configured
    # AGENT_OUTPUT_DIR/default value is a workspace root and is never cleared.
    if args.output:
        cfg.output_dir = Path(args.output).expanduser().resolve()
    else:
        task = str(getattr(args, "task", None) or "interactive-session")
        cfg.output_dir = (
            OutputWorkspace.from_root(cfg.output_dir)
            .allocate_run(
                task=task,
                model=cfg.model_name,
            )
            .root
        )


def _print_result(result: AgentResult, *, oneline: bool = False) -> None:
    if oneline:
        print(
            f"Status: {result.status} | Steps: {result.steps_taken} | "
            f"Duration: {result.total_duration:.1f}s"
        )
    else:
        print(f"Status: {result.status}")
        print(f"Steps: {result.steps_taken}")
        print(f"Duration: {result.total_duration:.2f}s")
    if result.final_result:
        print(f"Result: {result.final_result}")


async def _confirm_high_risk_action(tool_call: ToolCall, assessment: RiskAssessment) -> bool:
    """Request an explicit terminal confirmation without echoing sensitive parameters."""
    tool_name = str(getattr(tool_call, "tool_name", "unknown"))
    prompt = (
        f"High-risk action requested: {tool_name} ({assessment.external_effect}). Approve? [y/N] "
    )
    response = await asyncio.to_thread(input, prompt)
    return response.strip().casefold() in {"y", "yes"}


async def _interactive_session(agent: WebAgent) -> None:
    """Run tasks from stdin until the user quits."""
    print("webagent interactive session - type 'quit' to exit")
    first = True
    while True:
        prompt = "Task: " if first else "Follow-up: "
        user_input = input(prompt).strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break
        result = await agent.run(user_input, reset_history=first)
        first = False
        _print_result(result, oneline=True)


def _apply_resume_arguments(args: argparse.Namespace) -> str | None:
    """Validate a checkpoint and derive omitted task/output CLI arguments."""
    resume_path = getattr(args, "resume", None)
    if not resume_path:
        return None
    if getattr(args, "interactive", False):
        raise ValueError("--resume cannot be combined with --interactive")
    if not getattr(args, "task", None):
        raise ValueError("--resume requires --task because checkpoint files store only its hash")
    CheckpointStore(resume_path).load(expected_task=args.task)
    checkpoint_path = Path(resume_path).expanduser().resolve()
    inferred_output = RunLayout.root_from_checkpoint(checkpoint_path)
    if getattr(args, "output", None):
        if Path(args.output).expanduser().resolve() != inferred_output:
            raise ValueError("--output does not match the checkpoint run directory")
    else:
        args.output = str(inferred_output)
    return str(resume_path)


async def run_task(args: argparse.Namespace) -> None:
    resume_path = _apply_resume_arguments(args)

    cfg = AgentConfig()
    _apply_cli_overrides(cfg, args)
    if resume_path and cfg.strict_eval_mode:
        raise ValueError("strict-eval runs cannot resume from checkpoints")

    planner = _build_planner(cfg)
    await planner.load()

    browser = _build_browser(cfg)

    try:
        await browser.start()
    except Exception as exc:
        # start() may have launched Playwright before Chromium/profile setup
        # failed. Close that partial state so the next run is not reported as a
        # crashed browser session.
        try:
            await browser.close()
        finally:
            await planner.unload()
        raise RuntimeError(f"Browser failed to start: {exc}") from exc

    try:
        registry = _build_tool_registry(browser, cfg, planner)
        allowed_tools = allowed_tools_for_discovery_mode(registry.names(), cfg.discovery_mode)
        policy = None
        if cfg.search_engine_only:
            from webagent.tools.policy import SearchEngineOnlyPolicy

            policy = SearchEngineOnlyPolicy(browser, artifacts_dir=cfg.artifacts_dir)
        else:
            from webagent.tools.policy import BrowserGroundedPolicy

            policy_tools = allowed_tools or frozenset(registry.names())
            policy = BrowserGroundedPolicy(
                browser,
                artifacts_dir=cfg.artifacts_dir,
                allowed_tools=policy_tools,
                require_browser_search=cfg.discovery_mode == "browser-grounded",
                official_report_max_attempts=cfg.hybrid_official_report_max_attempts,
                evidence_repeat_limit=cfg.hybrid_evidence_repeat_limit,
            )
        risk_policy = ActionRiskPolicy(
            cfg.high_risk_action_policy,
            confirmer=_confirm_high_risk_action
            if cfg.high_risk_action_policy == "prompt"
            else None,
            context_provider=BrowserRiskContext(browser),
        )
        executor = ToolExecutor(
            registry,
            tool_timeout=cfg.tool_timeout,
            allowed_tools=allowed_tools,
            policy=policy,
            risk_policy=risk_policy,
        )

        agent = WebAgent(
            planner=planner,
            browser=browser,
            tool_executor=executor,
            config=cfg,
            output_dir=args.output,
        )
        agent.add_hook(LoggingHook())

        if args.interactive:
            await _interactive_session(agent)
        else:
            if not args.task:
                print("Error: --task required unless --interactive")
                return
            result = (
                await agent.run(args.task, resume_from=resume_path)
                if resume_path
                else await agent.run(args.task)
            )
            _print_result(result)
    finally:
        try:
            await browser.close()
        finally:
            await planner.unload()


def main() -> None:
    configure_logging()
    args = parse_args()
    asyncio.run(run_task(args))


if __name__ == "__main__":
    main()
