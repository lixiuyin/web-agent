"""CLI entry point for the webagent package."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from webagent.agent.hooks import LoggingHook
from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.planner.stub import StubPlanner
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry
from webagent.utils.logging import configure_logging


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
    p.add_argument("--interactive", action="store_true", help="Interactive mode")
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: from config AGENT_OUTPUT_DIR or ./outputs)",
    )
    p.add_argument("--model", type=str, help="Override model name (default: from config)")
    p.add_argument("--api-url", type=str, help="Override API URL (default: from config)")
    p.add_argument("--api-key", type=str, help="Override API key (default: from config)")
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
        "--version",
        action="version",
        version=f"%(prog)s {webagent.__version__}",
        help="Show version information",
    )
    return p.parse_args()


def _build_planner(cfg: AgentConfig):
    """Select planner based on config."""
    if cfg.model_api_url and cfg.model_api_key:
        from webagent.planner.api import APIPlanner

        return APIPlanner(
            api_url=cfg.model_api_url,
            api_key=cfg.model_api_key,
            model_name=cfg.model_name,
            timeout=cfg.api_timeout,
            hard_timeout=cfg.api_hard_timeout,
            use_structured_output=cfg.use_structured_output,
        )
    if cfg.use_vllm:
        from webagent.planner.api import APIPlanner

        logging.info(
            "No remote API credentials configured — using local vLLM endpoint %s",
            cfg.vllm_api_url,
        )
        return APIPlanner(
            api_url=cfg.vllm_api_url,
            api_key=cfg.vllm_api_key,
            model_name=cfg.vllm_model_name,
            timeout=cfg.api_timeout,
            hard_timeout=cfg.api_hard_timeout,
            use_structured_output=cfg.use_structured_output,
        )
    logging.warning(
        "No API credentials configured — using StubPlanner (no real planning). "
        "Set AGENT_MODEL_API_URL and AGENT_MODEL_API_KEY (or pass --api-url/--api-key) "
        "to enable the LLM planner, or pass --use-vllm for a local OpenAI-compatible "
        "vLLM server."
    )
    return StubPlanner()


def _build_tool_registry(browser: BrowserController, config: AgentConfig, planner) -> ToolRegistry:
    """Create tool registry with all built-in tools."""
    import webagent.tools.builtin  # noqa: F401 – triggers @tool registration

    registry = ToolRegistry()
    registry.auto_discover(browser=browser, config=config, planner=planner)
    return registry


async def run_task(args: argparse.Namespace) -> None:
    cfg = AgentConfig()
    if args.model:
        cfg.model_name = args.model
    if args.api_url:
        cfg.model_api_url = args.api_url
    if args.api_key:
        cfg.model_api_key = args.api_key
    if args.use_vllm is not None:
        cfg.use_vllm = args.use_vllm
    if args.vllm_model_name:
        cfg.vllm_model_name = args.vllm_model_name
    if args.vllm_api_url:
        cfg.vllm_api_url = args.vllm_api_url
    if args.vllm_api_key:
        cfg.vllm_api_key = args.vllm_api_key
    if args.headless:
        cfg.browser_headless = True
    if args.headed:
        cfg.browser_headless = False
    # Override output_dir from CLI args (supports env var AGENT_OUTPUT_DIR)
    if args.output:
        cfg.output_dir = Path(args.output)

    # Ensure artifacts directory exists
    cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)

    planner = _build_planner(cfg)
    await planner.load()

    browser = BrowserController(
        headless=cfg.browser_headless,
        viewport_width=cfg.viewport_width,
        viewport_height=cfg.viewport_height,
        default_timeout=cfg.browser_timeout,
    )

    try:
        await browser.start()
    except Exception as exc:
        await planner.unload()
        raise RuntimeError(f"Browser failed to start: {exc}") from exc

    try:
        registry = _build_tool_registry(browser, cfg, planner)
        executor = ToolExecutor(registry, tool_timeout=cfg.tool_timeout)

        agent = WebAgent(
            planner=planner,
            browser=browser,
            tool_executor=executor,
            config=cfg,
            output_dir=args.output,
        )
        agent.add_hook(LoggingHook())

        if args.interactive:
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
                print(
                    f"Status: {result.status} | Steps: {result.steps_taken} | Duration: {result.total_duration:.1f}s"
                )
                if result.final_result:
                    print(f"Result: {result.final_result}")
        else:
            if not args.task:
                print("Error: --task required unless --interactive")
                return
            result = await agent.run(args.task)
            print(f"Status: {result.status}")
            print(f"Steps: {result.steps_taken}")
            print(f"Duration: {result.total_duration:.2f}s")
            if result.final_result:
                print(f"Result: {result.final_result}")
    finally:
        await browser.close()
        await planner.unload()  # always runs even if browser.start() succeeded but later failed


def main() -> None:
    configure_logging()
    args = parse_args()
    asyncio.run(run_task(args))


if __name__ == "__main__":
    main()
