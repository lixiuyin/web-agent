"""Validate diagnostic plus BrowserGym evidence without pooling unlike scores."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from webagent.evaluation import load_two_layer_portfolio


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--external", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    report = load_two_layer_portfolio(args.diagnostic, args.external)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "status": report.status,
                "models": [model.model for model in report.models],
                "missing_requirements": report.missing_requirements,
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if args.require_ready and report.status != "ready" else 0


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
