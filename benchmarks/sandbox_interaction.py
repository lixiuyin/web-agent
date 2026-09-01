"""Compatibility wrapper for the controlled-web sandbox suite."""

from benchmarks.suites.controlled_web.sandbox import (
    SandboxHarnessBaselinePlanner,
    ScriptedSandboxPlanner,
    main,
    parse_args,
    run_benchmark,
)

if __name__ == "__main__":
    main()

__all__ = [
    "SandboxHarnessBaselinePlanner",
    "ScriptedSandboxPlanner",
    "parse_args",
    "run_benchmark",
]
