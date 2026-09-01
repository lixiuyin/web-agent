"""Compatibility wrapper for :mod:`benchmarks.suites.open_web.runner`.

Deprecated: import and execute the suite module directly. This wrapper remains
for one compatibility cycle.
"""

from benchmarks.suites.open_web.runner import (
    append_time_slice,
    benchmark_config_evidence,
    canonical_sha256,
    load_manifest,
    main,
    parse_args,
    require_free_space,
    retain_manifest_snapshot,
    retain_report_snapshot,
    run_benchmark,
)

if __name__ == "__main__":
    main()

__all__ = [
    "append_time_slice",
    "benchmark_config_evidence",
    "canonical_sha256",
    "load_manifest",
    "parse_args",
    "require_free_space",
    "retain_manifest_snapshot",
    "retain_report_snapshot",
    "run_benchmark",
]
