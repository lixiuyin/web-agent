"""Deduplicate byte-identical promoted shard evidence without deleting either path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(slots=True)
class CompactionResult:
    run_pairs: int = 0
    considered_files: int = 0
    identical_files: int = 0
    already_linked_files: int = 0
    linked_files: int = 0
    reclaimable_bytes: int = 0
    skipped_different_files: int = 0
    errors: int = 0


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _stable_digest(path: Path) -> tuple[os.stat_result, str]:
    def identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    before = path.stat()
    digest = _digest(path)
    after = path.stat()
    if identity(before) != identity(after):
        raise RuntimeError(f"file changed while hashing: {path}")
    return after, digest


def _normalized_trace(path: Path) -> tuple[str, str] | None:
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
        run_id = trace.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return None
        normalized = json.dumps(trace, ensure_ascii=False, sort_keys=True)
        normalized = normalized.replace(str(path.parent.parent.resolve()), "<RUN_ROOT>")
        return run_id, normalized
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _promoted_pairs(root: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    pattern = "shards/shard-*/runs/*/trajectory/trace.json"
    for shard_trace in sorted(root.rglob(pattern)):
        shard_run = shard_trace.parent.parent
        execution = shard_run.parents[3]
        canonical_run = execution / "runs" / shard_run.name
        canonical_trace = canonical_run / "trajectory" / "trace.json"
        if not canonical_trace.is_file():
            continue
        left = _normalized_trace(shard_trace)
        right = _normalized_trace(canonical_trace)
        if left is not None and left == right:
            pairs.append((shard_run, canonical_run))
    return pairs


def _share_file(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.link-{uuid4().hex}.tmp")
    try:
        os.link(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def compact(root: Path, *, apply: bool = False) -> CompactionResult:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"artifact root is not a directory: {root}")
    result = CompactionResult()
    for shard_run, canonical_run in _promoted_pairs(root):
        result.run_pairs += 1
        for source in sorted(path for path in shard_run.rglob("*") if path.is_file()):
            if source.is_symlink():
                continue
            target = canonical_run / source.relative_to(shard_run)
            if not target.is_file() or target.is_symlink():
                continue
            result.considered_files += 1
            try:
                source_stat, source_hash = _stable_digest(source)
                target_stat, target_hash = _stable_digest(target)
                if (
                    source_hash != target_hash
                    or stat.S_IMODE(source_stat.st_mode) != stat.S_IMODE(target_stat.st_mode)
                    or source_stat.st_mtime_ns != target_stat.st_mtime_ns
                ):
                    result.skipped_different_files += 1
                    continue
                result.identical_files += 1
                if (source_stat.st_dev, source_stat.st_ino) == (
                    target_stat.st_dev,
                    target_stat.st_ino,
                ):
                    result.already_linked_files += 1
                    continue
                result.reclaimable_bytes += target_stat.st_size
                if apply:
                    _share_file(source, target)
                    result.linked_files += 1
            except OSError:
                result.errors += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Campaign or execution root to inspect")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace byte-identical canonical copies with hardlinks to immutable shard files",
    )
    args = parser.parse_args()
    result = compact(args.root, apply=args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **asdict(result)}, indent=2))
    if result.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
