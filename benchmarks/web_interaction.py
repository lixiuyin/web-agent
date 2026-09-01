"""Compatibility wrapper for the controlled-web general suite."""

from benchmarks.core import BROWSER_ONLY_TOOLS
from benchmarks.suites.controlled_web.general import (
    HarnessBaselinePlanner,
    ScriptedBenchmarkPlanner,
    main,
    parse_args,
    run_benchmark,
)

_BROWSER_ONLY_TOOLS = BROWSER_ONLY_TOOLS

if __name__ == "__main__":
    main()

__all__ = [
    "HarnessBaselinePlanner",
    "ScriptedBenchmarkPlanner",
    "parse_args",
    "run_benchmark",
]
