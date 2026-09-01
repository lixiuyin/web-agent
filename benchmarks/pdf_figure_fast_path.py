"""Compatibility wrapper for the document-figure fast-path suite."""

from benchmarks.suites.document_figures.fast_path import (
    BenchmarkDocument,
    ExpectedFigure,
    build_benchmark_corpus,
    main,
    run_benchmark,
)

if __name__ == "__main__":
    main()

__all__ = [
    "BenchmarkDocument",
    "ExpectedFigure",
    "build_benchmark_corpus",
    "run_benchmark",
]
