"""Tests for the cascade's provider fallback / quality-gate orchestration."""

from __future__ import annotations

import pytest

from webagent.core.config import AgentConfig
from webagent.parser import cascade
from webagent.parser._errors import ParserProviderError
from webagent.parser.models import PDFParseResult, TextBlock


def _good(backend: str) -> PDFParseResult:
    r = PDFParseResult(None, None, "i", "o", backend=backend)
    r.text_blocks.append(
        TextBlock("a genuinely substantial paragraph of text " * 10, 0, (0, 0, 0, 0))
    )
    return r


def _empty(backend: str) -> PDFParseResult:
    # No text + no assets → fails the quality gate for a text PDF.
    return PDFParseResult(None, None, "i", "o", backend=backend)


class _FakeProvider:
    def __init__(self, name: str, behavior: str) -> None:
        self.name = name
        self.behavior = behavior
        self.calls = 0

    async def parse(self, client: object, req: object) -> PDFParseResult:
        self.calls += 1
        if self.behavior == "good":
            return _good(self.name)
        if self.behavior == "empty":
            return _empty(self.name)
        raise ParserProviderError(self.name, retryable=False)


@pytest.fixture
def pdf(tmp_path):
    import fitz

    p = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "real text " * 30)
    doc.save(str(p))
    doc.close()
    return p


def _patch(monkeypatch, providers: dict, order: tuple[str, ...]):
    monkeypatch.setattr(cascade, "_PROVIDERS", providers)
    monkeypatch.setattr(cascade, "select_parsers", lambda profile, user_hint="": order)


async def test_primary_success_wins(monkeypatch, pdf, tmp_path):
    marker = _FakeProvider("marker", "good")
    mineru = _FakeProvider("mineru", "good")
    _patch(monkeypatch, {"marker": marker, "mineru": mineru}, ("marker", "mineru"))

    result = await cascade.parse_structured_async(pdf, tmp_path / "out", config=AgentConfig())
    assert result.error is None
    assert result.backend == "marker"
    assert mineru.calls == 0  # secondary never tried


async def test_quality_gate_falls_through_to_next_provider(monkeypatch, pdf, tmp_path):
    marker = _FakeProvider("marker", "empty")  # passes HTTP but fails quality
    mineru = _FakeProvider("mineru", "good")
    _patch(monkeypatch, {"marker": marker, "mineru": mineru}, ("marker", "mineru"))

    result = await cascade.parse_structured_async(pdf, tmp_path / "out", config=AgentConfig())
    assert result.backend == "mineru"
    assert marker.calls == 1 and mineru.calls == 1


async def test_provider_error_falls_through(monkeypatch, pdf, tmp_path):
    marker = _FakeProvider("marker", "raise")
    mineru = _FakeProvider("mineru", "good")
    _patch(monkeypatch, {"marker": marker, "mineru": mineru}, ("marker", "mineru"))

    result = await cascade.parse_structured_async(pdf, tmp_path / "out", config=AgentConfig())
    assert result.backend == "mineru"


async def test_all_cloud_fail_falls_back_to_local(monkeypatch, pdf, tmp_path):
    marker = _FakeProvider("marker", "raise")
    mineru = _FakeProvider("mineru", "raise")
    _patch(monkeypatch, {"marker": marker, "mineru": mineru}, ("marker", "mineru"))

    result = await cascade.parse_structured_async(pdf, tmp_path / "out", config=AgentConfig())
    # Local PyMuPDF fallback extracts the real text we wrote into the PDF.
    assert result.error is None
    assert result.backend == "pymupdf"
    assert any("real text" in b.text for b in result.text_blocks)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
