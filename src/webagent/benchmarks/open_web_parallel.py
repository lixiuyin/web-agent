"""Compatibility wrapper for :mod:`webagent.benchmarks.suites.open_web.parallel`."""

from webagent.benchmarks.suites.open_web.parallel import (
    main,
    merge_shard_reports,
    parse_args,
    run_parallel,
)

if __name__ == "__main__":
    main()

__all__ = ["merge_shard_reports", "parse_args", "run_parallel"]
