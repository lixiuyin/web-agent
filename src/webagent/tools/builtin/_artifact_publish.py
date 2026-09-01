"""Atomic, immutable publication helpers for downloaded run artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_are_identical(first: Path, second: Path) -> bool:
    """Compare artifacts without trusting filenames or timestamps."""
    if second.is_symlink() or not second.is_file():
        return False
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
        return _sha256_file(first) == _sha256_file(second)
    except OSError:
        return False


@contextmanager
def temporary_artifact_path(target: Path) -> Iterator[Path]:
    """Yield a private same-filesystem path and remove it on every exit path."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def publish_immutable_artifact(temporary: Path, target: Path) -> bool:
    """Atomically publish; return true if an identical target already exists.

    ``temporary`` must be on the target filesystem. A hard link provides atomic
    no-replace semantics: a prior or concurrent different target raises
    ``FileExistsError`` and remains untouched.
    """
    try:
        os.link(temporary, target)
    except FileExistsError:
        if _files_are_identical(temporary, target):
            return True
        raise
    return False


def publish_immutable_bytes(payload: bytes, target: Path) -> bool:
    """Publish in-memory bytes with the same immutable artifact contract."""
    with temporary_artifact_path(target) as temporary:
        temporary.write_bytes(payload)
        return publish_immutable_artifact(temporary, target)


__all__ = [
    "publish_immutable_artifact",
    "publish_immutable_bytes",
    "temporary_artifact_path",
]
