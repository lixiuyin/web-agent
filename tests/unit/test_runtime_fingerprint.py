"""Tests for source-bound evaluation provenance."""

import re
from pathlib import Path

from webagent.utils.runtime import (
    _python_source_fingerprint,
    agent_source_fingerprint,
    benchmark_source_fingerprint,
    package_source_fingerprint,
)


def test_package_source_fingerprint_is_stable_sha256() -> None:
    first = package_source_fingerprint()
    second = package_source_fingerprint()

    assert first == second
    assert first == agent_source_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_benchmark_source_fingerprint_is_stable_sha256() -> None:
    first = benchmark_source_fingerprint()
    second = benchmark_source_fingerprint()

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_python_source_fingerprint_tracks_code_but_not_other_files(tmp_path: Path) -> None:
    source = tmp_path / "runner.py"
    unrelated = tmp_path / "README.md"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    unrelated.write_text("first\n", encoding="utf-8")

    original = _python_source_fingerprint(tmp_path)
    unrelated.write_text("second\n", encoding="utf-8")
    assert _python_source_fingerprint(tmp_path) == original

    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert _python_source_fingerprint(tmp_path) != original
