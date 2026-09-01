"""Deterministic local-web environments with independently verifiable state."""

from benchmarks.environments.controlled_web.general_site import benchmark_site
from benchmarks.environments.controlled_web.sandbox_site import (
    SandboxOrigins,
    sandbox_interaction_site,
)

__all__ = ["SandboxOrigins", "benchmark_site", "sandbox_interaction_site"]
