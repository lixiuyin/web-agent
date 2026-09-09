"""Compatibility wrapper for :mod:`webagent.benchmarks.studies.open_web_longitudinal`."""

from webagent.benchmarks.studies.open_web_longitudinal import (
    evidence_record_from_report,
    load_slices,
    main,
    parse_args,
    summarize_slices,
    verify_evidence_record,
)

if __name__ == "__main__":
    main()

__all__ = [
    "evidence_record_from_report",
    "load_slices",
    "parse_args",
    "summarize_slices",
    "verify_evidence_record",
]
