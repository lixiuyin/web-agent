"""Compatibility wrapper for :mod:`benchmarks.studies.open_web_matrix`."""

from benchmarks.studies.open_web_matrix import (
    append_ledger,
    ledger_record_from_report,
    main,
    parse_args,
    run_matrix,
)

if __name__ == "__main__":
    main()

__all__ = ["append_ledger", "ledger_record_from_report", "parse_args", "run_matrix"]
