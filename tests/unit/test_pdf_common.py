"""Tests for shared PDF-tool parsing, caching, and error normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from webagent.core.config import AgentConfig
from webagent.parser import PDFParseResult
from webagent.tools.builtin import _pdf_common
from webagent.utils.paths import get_pdf_extract_dir


@pytest.fixture(autouse=True)
def _clear_pdf_cache():
    _pdf_common.pdf_result_cache.clear()
    _pdf_common._pdf_parse_locks.clear()
    yield
    _pdf_common.pdf_result_cache.clear()
    _pdf_common._pdf_parse_locks.clear()


async def test_load_pdf_result_forwards_config_and_reuses_cache(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    config = AgentConfig(_env_file=None, marker_api_key="injected-key", persistent_pdf_cache=False)
    calls = []

    def fake_parse(path: Path, output_dir: Path, *, config: AgentConfig | None = None):
        calls.append((path, output_dir, config))
        return PDFParseResult(None, None, "images", str(output_dir), backend="marker")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)

    first, first_error = await _pdf_common.load_pdf_result(pdf, tmp_path, "pdf_test", config=config)
    second, second_error = await _pdf_common.load_pdf_result(
        pdf, tmp_path, "pdf_test", config=config
    )

    assert first_error is None and second_error is None
    assert first is second
    assert calls == [(pdf, get_pdf_extract_dir(tmp_path, pdf), config)]


async def test_non_strict_memory_cache_never_reuses_another_runs_artifact_paths(
    tmp_path, monkeypatch
):
    from webagent.parser import ImageInfo

    first_artifacts = tmp_path / "run-one" / "artifacts"
    second_artifacts = tmp_path / "run-two" / "artifacts"
    first_pdf = first_artifacts / "downloads" / "paper.pdf"
    second_pdf = second_artifacts / "downloads" / "paper.pdf"
    first_pdf.parent.mkdir(parents=True)
    second_pdf.parent.mkdir(parents=True)
    content = b"%PDF-1.4 identical content"
    first_pdf.write_bytes(content)
    second_pdf.write_bytes(content)
    calls: list[Path] = []

    def fake_parse(path: Path, output_dir: Path, *, config=None):
        calls.append(output_dir)
        image = output_dir / "images" / "figure.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"png")
        result = PDFParseResult(None, None, str(image.parent), str(output_dir), backend="marker")
        result.images.append(ImageInfo(str(image), 0, (0, 0, 1, 1)))
        return result

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
    config = AgentConfig(_env_file=None, strict_eval_mode=False, persistent_pdf_cache=False)

    first, first_error = await _pdf_common.load_pdf_result(
        first_pdf, first_artifacts, "first", config=config
    )
    second, second_error = await _pdf_common.load_pdf_result(
        second_pdf, second_artifacts, "second", config=config
    )

    assert first_error is None and second_error is None
    assert first is not None and second is not None and second is not first
    assert len(calls) == 2
    assert Path(first.images[0].path).is_relative_to(first_artifacts)
    assert Path(second.images[0].path).is_relative_to(second_artifacts)
    assert not Path(second.images[0].path).is_relative_to(first_artifacts)


async def test_load_pdf_result_returns_provider_error(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    def fake_parse(path, output_dir, *, config=None):
        return PDFParseResult(None, None, "images", str(output_dir), error="provider failed")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)

    result, error = await _pdf_common.load_pdf_result(pdf, tmp_path, "pdf_test")

    assert result is None
    assert error is not None
    assert error.tool_name == "pdf_test"
    assert error.error == "provider failed"


async def test_load_pdf_result_normalizes_unexpected_exception(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    def fake_parse(path, output_dir, *, config=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)

    result, error = await _pdf_common.load_pdf_result(pdf, tmp_path, "pdf_test")

    assert result is None
    assert error is not None
    assert error.error == "Failed to parse PDF: unexpected"


async def test_concurrent_loads_share_one_parse(tmp_path, monkeypatch):
    import asyncio
    import time

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF single flight")
    calls = 0

    def fake_parse(path, output_dir, *, config=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return PDFParseResult(None, None, str(output_dir), str(output_dir), backend="marker")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
    config = AgentConfig(_env_file=None, persistent_pdf_cache=False)

    first, second = await asyncio.gather(
        _pdf_common.load_pdf_result(pdf, tmp_path, "one", config=config),
        _pdf_common.load_pdf_result(pdf, tmp_path, "two", config=config),
    )

    assert first[1] is None and second[1] is None
    assert first[0] is second[0]
    assert calls == 1


async def test_persistent_cache_rehydrates_into_new_artifacts_dir(tmp_path, monkeypatch):
    from webagent.parser import ImageInfo

    first_artifacts = tmp_path / "run-one" / "artifacts"
    second_artifacts = tmp_path / "run-two" / "artifacts"
    first_artifacts.mkdir(parents=True)
    second_artifacts.mkdir(parents=True)
    first_pdf = first_artifacts / "paper.pdf"
    second_pdf = second_artifacts / "paper.pdf"
    content = b"%PDF persistent cache"
    first_pdf.write_bytes(content)
    second_pdf.write_bytes(content)
    calls = 0

    def fake_parse(path, output_dir, *, config=None):
        nonlocal calls
        calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown = output_dir / "parsed.md"
        image = output_dir / "images" / "figure.jpg"
        image.parent.mkdir()
        markdown.write_text("# Cached", encoding="utf-8")
        image.write_bytes(b"jpeg")
        result = PDFParseResult(
            str(markdown), None, str(image.parent), str(output_dir), backend="marker"
        )
        result.images.append(ImageInfo(str(image), 0, (0, 0, 1, 1), caption="Figure 1"))
        return result

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
    config = AgentConfig(
        _env_file=None,
        persistent_pdf_cache=True,
        pdf_cache_dir=tmp_path / "cache",
    )

    first, first_error = await _pdf_common.load_pdf_result(
        first_pdf, first_artifacts, "first", config=config
    )
    assert first_error is None and first is not None
    _pdf_common.pdf_result_cache.clear()
    _pdf_common._pdf_parse_locks.clear()
    second, second_error = await _pdf_common.load_pdf_result(
        second_pdf, second_artifacts, "second", config=config
    )

    assert second_error is None and second is not None
    assert calls == 1
    assert Path(second.markdown_path or "").is_file()
    assert Path(second.images[0].path).is_file()
    assert str(second_artifacts) in second.images[0].path


async def test_strict_mode_does_not_reuse_another_runs_memory_cache(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF strict cache isolation")
    stale = PDFParseResult(None, None, "old", "old", backend="marker")
    _pdf_common.pdf_result_cache[_pdf_common.pdf_cache_key(pdf)] = stale
    calls = 0

    def fake_parse(path, output_dir, *, config=None):
        nonlocal calls
        calls += 1
        return PDFParseResult(None, None, str(output_dir), str(output_dir), backend="marker")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
    config = AgentConfig(_env_file=None, strict_eval_mode=True, persistent_pdf_cache=False)

    result, error = await _pdf_common.load_pdf_result(
        pdf, tmp_path / "fresh-artifacts", "strict", config=config
    )

    assert error is None and result is not None
    assert result is not stale
    assert calls == 1


async def test_corrupt_persistent_manifest_cannot_escape_output(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF cache traversal")
    fingerprint = _pdf_common.pdf_cache_key(pdf)
    cache_entry = tmp_path / "cache" / fingerprint
    (cache_entry / "files").mkdir(parents=True)
    (cache_entry / "manifest.json").write_text(
        '{"version":1,"markdown_path":"../../outside.md","images":[],"tables":[],'
        '"text_blocks":[],"sections":{}}',
        encoding="utf-8",
    )
    calls = 0

    def fake_parse(path, output_dir, *, config=None):
        nonlocal calls
        calls += 1
        return PDFParseResult(None, None, str(output_dir), str(output_dir), backend="marker")

    monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
    config = AgentConfig(
        _env_file=None,
        persistent_pdf_cache=True,
        pdf_cache_dir=tmp_path / "cache",
    )

    result, error = await _pdf_common.load_pdf_result(pdf, tmp_path / "run", "safe", config=config)

    assert error is None and result is not None
    assert calls == 1
    assert not (tmp_path / "outside.md").exists()
