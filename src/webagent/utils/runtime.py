"""Reproducibility fingerprints for installed agent and benchmark sources."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


def _python_source_fingerprint(
    package_root: Path, *, excluded_directories: tuple[str, ...] = ()
) -> str:
    """Hash Python paths and bytes below one package root deterministically."""
    source_paths = sorted(
        path
        for path in package_root.rglob("*.py")
        if path.relative_to(package_root).parts[0] not in excluded_directories
    )
    if not source_paths:
        raise RuntimeError(f"no Python sources found below {package_root}")
    digest = hashlib.sha256()
    for path in source_paths:
        relative = path.relative_to(package_root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def agent_source_fingerprint() -> str:
    """Hash runtime and shared evaluation Python sources, excluding benchmarks."""
    package_root = Path(__file__).resolve().parents[1]
    return _python_source_fingerprint(package_root, excluded_directories=("benchmarks",))


def _benchmarks_package_root() -> Path:
    """Locate the benchmark subpackage in a source checkout or installed wheel."""
    benchmarks_root = Path(__file__).resolve().parents[1] / "benchmarks"
    if not (benchmarks_root / "__init__.py").is_file():
        raise RuntimeError("benchmarks subpackage could not be located inside webagent")
    return benchmarks_root


@lru_cache(maxsize=1)
def benchmark_source_fingerprint() -> str:
    """Hash executable benchmark harness, suite, environment, and study code."""
    return _python_source_fingerprint(_benchmarks_package_root())


def package_source_fingerprint() -> str:
    """Compatibility name for :func:`agent_source_fingerprint`."""
    return agent_source_fingerprint()


__all__ = [
    "agent_source_fingerprint",
    "benchmark_source_fingerprint",
    "package_source_fingerprint",
]
