"""Construct configured planner implementations for CLIs and benchmark adapters."""

from __future__ import annotations

import logging

from webagent.core.config import AgentConfig
from webagent.core.protocols import Planner
from webagent.planner.stub import StubPlanner

logger = logging.getLogger(__name__)


def build_planner(config: AgentConfig) -> Planner:
    """Return the configured remote, local-vLLM, or explicit stub planner."""
    if config.model_api_url and config.model_api_key:
        return _api_planner(
            config,
            api_url=config.model_api_url,
            api_key=config.model_api_key,
            model_name=config.model_name,
        )
    if config.use_vllm:
        logger.info(
            "No remote API credentials configured - using local vLLM endpoint %s",
            config.vllm_api_url,
        )
        return _api_planner(
            config,
            api_url=config.vllm_api_url,
            api_key=config.vllm_api_key,
            model_name=config.vllm_model_name,
        )
    logger.warning(
        "No API credentials configured - using StubPlanner (no real planning). "
        "Set AGENT_MODEL_API_URL and AGENT_MODEL_API_KEY, or enable local vLLM."
    )
    return StubPlanner()


def _api_planner(
    config: AgentConfig,
    *,
    api_url: str,
    api_key: str,
    model_name: str,
) -> Planner:
    from webagent.planner.api import APIPlanner

    return APIPlanner(
        api_url=api_url,
        api_key=api_key,
        model_name=model_name,
        timeout=config.api_timeout,
        hard_timeout=config.api_hard_timeout,
        transient_retries=config.api_transient_retries,
        retry_base_seconds=config.api_retry_base_seconds,
        retry_max_seconds=config.api_retry_max_seconds,
        use_structured_output=config.use_structured_output,
        output_mode=config.planner_output_mode,
        max_tokens=config.planner_max_tokens,
        reasoning_effort=config.planner_reasoning_effort,
        screenshot_mode=config.planner_screenshot_mode,
        vision_max_tokens=config.vision_max_tokens,
        vision_brief_max_tokens=config.vision_brief_max_tokens,
        vision_max_words=config.vision_max_words,
    )


__all__ = ["build_planner"]
