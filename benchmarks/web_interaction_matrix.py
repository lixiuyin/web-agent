"""Compatibility wrapper for the controlled-web repeated-model study."""

from benchmarks.studies.controlled_web_matrix import (
    aggregate_reports,
    main,
    parse_args,
    run_matrix,
)

if __name__ == "__main__":
    main()

__all__ = ["aggregate_reports", "parse_args", "run_matrix"]
