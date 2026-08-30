"""Tests for the arXiv API search tool."""

from __future__ import annotations

import pytest

from webagent.tools.builtin import arxiv_tools
from webagent.tools.builtin.arxiv_tools import ArxivSearchTool, _build_search_query

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <published>2024-01-15T00:00:00Z</published>
    <title>Qwen Technical Report</title>
    <summary>We present Qwen, a series of large language models.</summary>
    <author><name>Jinze Bai</name></author>
    <author><name>Shuai Bai</name></author>
    <link href="http://arxiv.org/abs/2401.00001v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


class _Resp:
    def __init__(self, status: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, **kwargs: object) -> _Resp:
        return self._resp


def _patch(monkeypatch, resp: _Resp) -> None:
    monkeypatch.setattr(arxiv_tools.httpx, "AsyncClient", lambda *a, **k: _Client(resp))

    async def _nosleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(arxiv_tools.asyncio, "sleep", _nosleep)


def test_parse_extracts_fields():
    results = ArxivSearchTool._parse(_ATOM)
    assert len(results) == 1
    r = results[0]
    assert r["title"] == "Qwen Technical Report"
    assert r["authors"] == ["Jinze Bai", "Shuai Bai"]
    assert r["published"] == "2024-01-15"
    # http pdf link upgraded to https
    assert r["pdf_url"] == "https://arxiv.org/pdf/2401.00001v1"
    assert "large language models" in r["abstract"]


def test_technical_report_query_is_title_scoped():
    query = _build_search_query("Qwen3 technical report")
    assert query == "ti:Qwen3 AND ti:technical AND ti:report"


def test_general_query_remains_all_field_scoped():
    assert _build_search_query("sparse attention") == "all:sparse AND all:attention"


async def test_successful_search(monkeypatch):
    _patch(monkeypatch, _Resp(200, _ATOM))
    tool = ArxivSearchTool()
    result = await tool.execute({"query": "attention"})
    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["results"][0]["pdf_url"].endswith("2401.00001v1")


async def test_qwen_query_uses_live_api(monkeypatch):
    """Qwen queries are not special-cased — they hit the arXiv export API like any other."""
    _patch(monkeypatch, _Resp(200, _ATOM))
    result = await ArxivSearchTool().execute({"query": "Qwen", "max_results": 2})

    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["results"][0]["title"] == "Qwen Technical Report"


async def test_rate_limited_returns_clear_error(monkeypatch):
    _patch(monkeypatch, _Resp(429, "Too Many Requests", {"retry-after": "30"}))
    tool = ArxivSearchTool()
    result = await tool.execute({"query": "attention"})
    assert result.success is False
    assert "rate-limited" in (result.error or "").lower()
    assert "429" in (result.error or "")


async def test_validation_requires_query():
    tool = ArxivSearchTool()
    with pytest.raises(ValueError):
        tool.validate_params({})


async def test_empty_feed_reports_no_results(monkeypatch):
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    _patch(monkeypatch, _Resp(200, empty))
    result = await ArxivSearchTool().execute({"query": "zzzznotarealpaper"})
    assert result.success is False
    assert "no arxiv results" in (result.error or "").lower()


async def test_transport_error_is_retried_and_diagnosable(monkeypatch):
    """A ReadTimeout('') must be retried and still produce a non-empty error."""
    import httpx

    calls = {"n": 0}

    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            calls["n"] += 1
            raise httpx.ReadTimeout("")  # empty-string exception

    monkeypatch.setattr(arxiv_tools.httpx, "AsyncClient", lambda *a, **k: _RaisingClient())

    async def _nosleep(_s):
        return None

    monkeypatch.setattr(arxiv_tools.asyncio, "sleep", _nosleep)

    result = await ArxivSearchTool().execute({"query": "attention"})
    assert result.success is False
    assert "ReadTimeout" in (result.error or "")  # type surfaced despite empty str
    assert calls["n"] == arxiv_tools._RETRIES + 1  # retried, not one-shot
