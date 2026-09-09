"""Verify a retained study ledger and report paired intervention transfer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from webagent.evaluation.transfer import analyze_study_intervention_transfer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_root", type=Path)
    parser.add_argument("--baseline-condition", required=True)
    parser.add_argument("--intervention-condition", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional immutable JSON output; verified analysis is always printed",
    )
    return parser.parse_args(argv)


def _encoded(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"


def _publish_immutable(path: Path, payload: bytes) -> None:
    target = path.expanduser().resolve()
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError(f"analysis output already exists with different bytes: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    analysis = analyze_study_intervention_transfer(
        args.study_root,
        baseline_condition_id=args.baseline_condition,
        intervention_condition_id=args.intervention_condition,
    )
    encoded = _encoded(analysis.model_dump(mode="json"))
    if args.output is not None:
        _publish_immutable(args.output, encoded)
    print(encoded.decode(), end="")


if __name__ == "__main__":
    main()
