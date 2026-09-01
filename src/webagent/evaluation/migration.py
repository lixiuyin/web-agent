"""Lossless migration of pre-workspace outputs into an immutable legacy tree.

The migrator deliberately does not reinterpret old traces or invent missing
study metadata.  It records every file's original relative path, byte length,
and SHA-256 digest, moves complete top-level entries on the same filesystem,
then verifies the published bytes.  A failed verification rolls moved entries
back to their original locations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_RESERVED_NAMES = frozenset({"legacy", "runs", "studies", "campaigns", "cache", "tmp"})
_LABEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@dataclass(frozen=True, slots=True)
class LegacyFileRecord:
    """Integrity record for one historical output file."""

    source_path: str
    archived_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LegacyMigrationPlan:
    """A deterministic migration plan generated before any path is moved."""

    output_root: Path
    target: Path
    entries: tuple[Path, ...]
    files: tuple[LegacyFileRecord, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


def plan_legacy_migration(output_root: Path, *, label: str) -> LegacyMigrationPlan:
    """Inventory legacy top-level entries without modifying the filesystem."""
    root = output_root.expanduser().resolve()
    _validate_label(label)
    target = root / "legacy" / label
    entries = tuple(
        path
        for path in sorted(root.iterdir() if root.is_dir() else (), key=lambda item: item.name)
        if path.name not in _RESERVED_NAMES
    )
    records: list[LegacyFileRecord] = []
    for entry in entries:
        _reject_symlinks(entry)
        candidates = [entry] if entry.is_file() else sorted(entry.rglob("*"))
        for path in candidates:
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"unsupported legacy output entry: {path}")
            relative = path.relative_to(root)
            records.append(
                LegacyFileRecord(
                    source_path=relative.as_posix(),
                    archived_path=(Path("tree") / relative).as_posix(),
                    size=path.stat().st_size,
                    sha256=_sha256(path),
                )
            )
    return LegacyMigrationPlan(
        output_root=root,
        target=target,
        entries=entries,
        files=tuple(records),
    )


def migrate_legacy_outputs(output_root: Path, *, label: str) -> Path:
    """Move and hash-verify legacy entries, returning the migration manifest.

    The target must not already exist.  This fail-closed rule prevents a second
    invocation from merging unrelated bytes into an existing historical set.
    When no legacy entries remain and the target already contains a valid
    manifest, the operation is idempotent and returns that manifest.
    """
    plan = plan_legacy_migration(output_root, label=label)
    manifest_path = plan.target / "migration-manifest.json"
    if plan.target.exists():
        if not plan.entries and manifest_path.is_file():
            _verify_persisted_manifest(plan.output_root, manifest_path)
            return manifest_path
        raise FileExistsError(f"legacy migration target already exists: {plan.target}")
    if not plan.entries:
        raise ValueError("no legacy output entries found")

    legacy_root = plan.output_root / "legacy"
    legacy_root.mkdir(parents=True, exist_ok=True)
    staging = legacy_root / f".{label}-{uuid4().hex}.tmp"
    tree = staging / "tree"
    tree.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        for source in plan.entries:
            destination = tree / source.name
            source.replace(destination)
            moved.append((source, destination))
        _verify_records(staging, plan.files)
        manifest = {
            "schema_version": 1,
            "kind": "webagent-legacy-output-migration",
            "created_at": datetime.now(UTC).isoformat(),
            "source_root": str(plan.output_root),
            "archived_tree": "tree",
            "entry_count": len(plan.entries),
            "file_count": len(plan.files),
            "total_bytes": plan.total_bytes,
            "files": [asdict(item) for item in plan.files],
        }
        (staging / "migration-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        staging.replace(plan.target)
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                destination.replace(source)
        if staging.exists():
            _remove_empty_tree(staging)
        raise
    _verify_persisted_manifest(plan.output_root, manifest_path)
    return manifest_path


def _verify_persisted_manifest(output_root: Path, manifest_path: Path) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_records = payload.get("files")
    if not isinstance(raw_records, list):
        raise ValueError("legacy migration manifest has no files list")
    records = tuple(LegacyFileRecord(**item) for item in raw_records)
    _verify_records(manifest_path.parent, records)
    if payload.get("file_count") != len(records):
        raise ValueError("legacy migration manifest file count is inconsistent")
    if payload.get("total_bytes") != sum(item.size for item in records):
        raise ValueError("legacy migration manifest byte count is inconsistent")
    expected_root = str(output_root.expanduser().resolve())
    if payload.get("source_root") != expected_root:
        raise ValueError("legacy migration manifest belongs to another output root")


def _verify_records(archive_root: Path, records: tuple[LegacyFileRecord, ...]) -> None:
    for record in records:
        path = (archive_root / record.archived_path).resolve()
        safe_root = archive_root.resolve()
        if path != safe_root and not path.is_relative_to(safe_root):
            raise ValueError(f"archived path escapes migration root: {record.archived_path}")
        if not path.is_file():
            raise ValueError(f"archived file is missing: {record.archived_path}")
        if path.stat().st_size != record.size or _sha256(path) != record.sha256:
            raise ValueError(f"archived file failed integrity verification: {record.archived_path}")


def _reject_symlinks(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"legacy migration refuses symbolic links: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ValueError(f"legacy migration refuses symbolic links: {child}")


def _validate_label(label: str) -> None:
    if not label or any(char not in _LABEL_CHARS for char in label):
        raise ValueError("migration label may contain only letters, digits, '-' and '_'")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_empty_tree(path: Path) -> None:
    """Remove only empty rollback directories; never recursively delete data."""
    for directory, _children, _files in os.walk(path, topdown=False):
        try:
            Path(directory).rmdir()
        except OSError:
            pass


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path, nargs="?", default=Path("outputs"))
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the verified move; without this flag only print the inventory plan",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    plan = plan_legacy_migration(args.output_root, label=args.label)
    if args.apply:
        manifest = migrate_legacy_outputs(args.output_root, label=args.label)
        persisted = json.loads(manifest.read_text(encoding="utf-8"))
        print(
            f"Verified legacy archive with {persisted['file_count']} files "
            f"({persisted['total_bytes']} bytes): {manifest}"
        )
        return
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "target": str(plan.target),
                "entries": [path.name for path in plan.entries],
                "file_count": len(plan.files),
                "total_bytes": plan.total_bytes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "LegacyFileRecord",
    "LegacyMigrationPlan",
    "migrate_legacy_outputs",
    "plan_legacy_migration",
]
