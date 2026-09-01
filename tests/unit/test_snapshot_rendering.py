"""Tests for snapshot HTML sanitization and markdown rendering."""

from __future__ import annotations

from typing import Any

from webagent.agent.hooks import LoggingHook
from webagent.browser.snapshot import (
    _filter_and_dedupe,
    _generate_llm_markdown,
    _interactive_controls_lines,
    _page_structure_lines,
    _sanitize_html,
)


class TestSanitizeHtml:
    def test_removes_script_and_style(self) -> None:
        html = "<html><body><script>evil()</script><style>a{}</style><p>text</p></body></html>"
        out = _sanitize_html(html)
        assert "evil()" not in out
        assert "a{}" not in out
        assert "text" in out

    def test_removes_ad_containers_when_filtering(self) -> None:
        html = (
            '<body><div id="ad-banner">Ad</div><div class="promo-box">Promo</div><p>ok</p></body>'
        )
        out = _sanitize_html(html, filter_ads=True)
        assert "Ad" not in out and "Promo" not in out
        assert "ok" in out

    def test_keeps_ads_when_filter_disabled(self) -> None:
        html = '<body><div id="ad-banner">Ad</div></body>'
        out = _sanitize_html(html, filter_ads=False)
        assert "Ad" in out

    def test_handles_malformed_html(self) -> None:
        out = _sanitize_html("<div><p>unclosed")
        assert "unclosed" in out


class TestPageStructureLines:
    def test_title_and_headings(self) -> None:
        html = "<html><head><title>My Page</title></head><body><h1>Head One</h1><h2>Head Two</h2></body></html>"
        lines = _page_structure_lines(_soup(html))
        assert lines[0] == "# My Page\n"
        assert any("Head One" in ln for ln in lines)
        assert any("## Head Two" in ln for ln in lines)

    def test_paragraphs_lists_and_structural(self) -> None:
        html = (
            "<body><p>Intro text.</p><ul><li>item one</li><li>item two</li></ul>"
            "<div><h3>Sub</h3><p>inner</p></div></body>"
        )
        lines = _page_structure_lines(_soup(html))
        assert any("Intro text." in ln for ln in lines)
        assert any("- item one" in ln for ln in lines)
        assert any("inner" in ln for ln in lines)

    def test_empty_paragraph_skipped(self) -> None:
        html = "<body><p>  </p></body>"
        assert _page_structure_lines(_soup(html)) == []


class TestInteractiveControlsLines:
    def test_lists_elements_with_priority_and_selector(self) -> None:
        elements = [
            {"tag": "input", "text": "Search", "attrs": {}, "_priority": 80, "css_path": "#q"},
            {"tag": "a", "text": "Home", "attrs": {}, "_priority": 40, "css_path": "a.first"},
        ]
        lines = _interactive_controls_lines(elements, max_elements=50)
        assert any("Search" in ln and "control 1" in ln for ln in lines)
        assert any("Home" in ln and "control 2" in ln for ln in lines)

    def test_ellipsis_for_hidden_elements(self) -> None:
        elements = [{"tag": "a", "text": f"L{i}", "attrs": {}} for i in range(5)]
        lines = _interactive_controls_lines(elements, max_elements=2)
        assert any("3 more interactive element" in ln for ln in lines)

    def test_no_elements_no_ellipsis(self) -> None:
        lines = _interactive_controls_lines([], max_elements=5)
        assert not any("more interactive" in ln for ln in lines)


class TestGenerateLlmMarkdown:
    def test_combines_structure_and_controls(self) -> None:
        html = "<html><head><title>T</title></head><body><h1>H</h1></body></html>"
        elements = [
            {"tag": "button", "text": "Go", "attrs": {}, "_priority": 50, "css_path": "#go"}
        ]
        md = _generate_llm_markdown(html, elements)
        assert "# T" in md
        assert "## Interactive Controls" in md
        assert "Go" in md


class TestFilterAndDedupe:
    def test_filters_ad_elements(self) -> None:
        raw = [
            {"tag": "div", "text": "Buy now", "attrs": {"class": "ad-box"}},
            {"tag": "a", "text": "Link", "attrs": {}},
        ]
        out = _filter_and_dedupe(raw, filter_ads=True)
        assert [e["text"] for e in out] == ["Link"]

    def test_dedupes_identical_signatures(self) -> None:
        raw = [
            {"tag": "a", "text": "Same", "attrs": {"id": "x"}, "bbox": {}},
            {"tag": "a", "text": "Same", "attrs": {"id": "x"}, "bbox": {}},
        ]
        assert len(_filter_and_dedupe(raw)) == 1

    def test_keeps_distinct_texts(self) -> None:
        raw = [
            {"tag": "a", "text": "One", "attrs": {"id": "x"}},
            {"tag": "a", "text": "Two", "attrs": {"id": "x"}},
        ]
        assert len(_filter_and_dedupe(raw)) == 2


class TestLoggingHook:
    async def test_hooks_log_success_step(self) -> None:
        from webagent.core.models import ToolCall, ToolResult

        hook = LoggingHook()
        call = ToolCall(
            tool_name="click",
            parameters={"selector": "#x", "note": "y" * 100},
            reasoning="click the button",
        )
        result = ToolResult(success=True, tool_name="click", data={"summary": "s" * 200})
        await hook.on_task_start("do a thing")
        await hook.on_step_complete(1, call, result)
        await hook.on_task_end("completed", 3)

    async def test_hooks_log_failed_step(self) -> None:
        from webagent.core.models import ToolCall, ToolResult

        hook = LoggingHook(verbose=False)
        call = ToolCall(tool_name="goto", parameters={}, reasoning="")
        result = ToolResult(success=False, tool_name="goto", error="timeout")
        await hook.on_step_complete(2, call, result)

    async def test_format_helpers(self) -> None:
        hook = LoggingHook()
        assert hook._format_params({}) == "{}"
        assert hook._format_data({}) == "{}"
        assert "..." in hook._format_params({"k": "v" * 200})

    async def test_format_pdf_parse_uses_compact_evidence_projection(self) -> None:
        hook = LoggingHook()
        rendered = hook._format_data(
            {
                "markdown": "very long " * 1000,
                "image_count": 1,
                "images": [{"figure_number": "1", "caption": "Architecture"}],
            },
            "pdf_parse",
        )
        assert "Architecture" in rendered
        assert "very long" not in rendered


def _soup(html: str) -> Any:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")
