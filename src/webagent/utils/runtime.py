"""Reproducibility fingerprints for installed agent and benchmark sources."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


def _python_source_fingerprint(package_root: Path) -> str:
    """Hash Python paths and bytes below one package root deterministically."""
    source_paths = sorted(package_root.rglob("*.py"))
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
    """Hash every Python source file in the active ``webagent`` package."""
    package_root = Path(__file__).resolve().parents[1]
    return _python_source_fingerprint(package_root)


def _benchmarks_package_root() -> Path:
    """Locate the benchmark package in a source checkout or installed wheel."""
    webagent_root = Path(__file__).resolve().parents[1]
    candidates = (
        webagent_root.parent / "benchmarks",
        webagent_root.parent.parent / "benchmarks",
    )
    for candidate in candidates:
        if (candidate / "__init__.py").is_file():
            return candidate
    raise RuntimeError("installed benchmarks package could not be located beside webagent")


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
