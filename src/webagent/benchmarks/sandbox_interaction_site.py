"""Compatibility wrapper for the controlled-web sandbox environment."""

from webagent.benchmarks.environments.controlled_web.sandbox_site import (
    SandboxOrigins,
    sandbox_interaction_site,
)

__all__ = ["SandboxOrigins", "sandbox_interaction_site"]
