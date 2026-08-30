"""Build a fail-closed cross-suite generality and long-horizon evidence report."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from webagent.evaluation.portfolio import load_empirical_portfolio, write_empirical_portfolio


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-models", type=int, default=2)
    parser.add_argument("--minimum-dates", type=int, default=3)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    report = load_empirical_portfolio(
        args.reports,
        minimum_models=args.minimum_models,
        minimum_dates=args.minimum_dates,
    )
    write_empirical_portfolio(report, args.output)
    print(
        json.dumps(
            {
                "status": report.status,
                "common_complete_dates": report.common_complete_dates,
                "missing_requirements": report.missing_requirements,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report.status == "ready" else 1)


if __name__ == "__main__":
    main()
