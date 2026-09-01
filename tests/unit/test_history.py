"""Tests for session history."""

from webagent.agent.history import SessionHistory
from webagent.core.models import AgentStep, BrowserState, ToolCall, ToolResult


def _make_step(n: int, tool: str = "goto", success: bool = True) -> AgentStep:
    return AgentStep(
        step_number=n,
        timestamp="2024-01-01",
        browser_state=BrowserState(
            dom_summary="", url="https://example.com", title="", timestamp="2024-01-01"
        ),
        tool_call=ToolCall(tool_name=tool, parameters={"url": "https://example.com"}),
        tool_result=ToolResult(
            success=success, tool_name=tool, data={"url": "https://example.com"}
        ),
        duration_seconds=0.5,
    )


def test_empty_history():
    h = SessionHistory()
    assert h.format_for_llm() == "No previous actions."
    assert h.steps == []


def test_add_and_format():
    h = SessionHistory(context_length=5)
    h.add(_make_step(1))
    h.add(_make_step(2, "click"))
    text = h.format_for_llm()
    assert "Step 1:" in text
    assert "Step 2:" in text
    assert "click" in text


def test_context_length_truncation():
    h = SessionHistory(context_length=2)
    for i in range(5):
        h.add(_make_step(i + 1))
    text = h.format_for_llm()
    assert "Step 4:" in text
    assert "Step 5:" in text
    assert "Step 1:" not in text


def test_old_results_are_summarized_while_recent_evidence_stays_full():
    h = SessionHistory(context_length=5, full_result_steps=1)
    old = _make_step(1, "search")
    old.tool_result.data = {
        "query": "old query",
        "engine": "bing",
        "results": [{"url": "https://old.example/candidate"}],
    }
    recent = _make_step(2, "search")
    recent.tool_result.data = {
        "query": "recent query",
        "engine": "duckduckgo",
        "results": [{"url": "https://recent.example/candidate"}],
    }
    h.add(old)
    h.add(recent)

    text = h.format_for_llm()

    assert "full result evidence recorded" in text
    assert '"result_count": 1' in text
    assert "https://old.example/candidate" not in text
    assert "https://recent.example/candidate" in text


def test_search_market_is_retained_in_planner_visible_evidence() -> None:
    history = SessionHistory()
    step = _make_step(1, "search")
    step.tool_result.data = {
        "query": "official docs",
        "engine": "bing",
        "search_market": "en-US",
        "results": [{"url": "https://example.test/docs"}],
    }
    history.add(step)

    assert '"search_market": "en-US"' in history.format_for_llm()


def test_clear():
    h = SessionHistory()
    h.add(_make_step(1))
    h.clear()
    assert h.steps == []


def test_policy_progress_is_visible_to_planner_history():
    h = SessionHistory()
    step = _make_step(1, "search")
    step.tool_result.audit = {
        "latest_evidence_complete": False,
        "latest_missing_prerequisites": [
            "a non-site official identity search",
            "a literal 2026 owner scope search",
        ],
    }
    h.add(step)

    text = h.format_for_llm()

    assert "policy still requires" in text
    assert "official identity" in text
    assert "literal 2026" in text


def test_completed_policy_checklist_is_visible_to_planner_history():
    h = SessionHistory()
    step = _make_step(1, "search")
    step.tool_result.audit = {
        "latest_evidence_complete": True,
        "latest_missing_prerequisites": [],
    }
    h.add(step)

    assert "latest-evidence checklist complete" in h.format_for_llm()


def test_policy_required_next_action_is_visible_to_planner_history():
    h = SessionHistory()
    step = _make_step(1, "search")
    step.tool_result.audit = {
        "latest_evidence_complete": True,
        "latest_missing_prerequisites": [],
        "required_next_action": {
            "tool": "download_pdf",
            "parameters": {"url": "https://example.test/report.pdf"},
        },
    }
    h.add(step)

    text = h.format_for_llm()

    assert "policy required next action" in text
    assert "download_pdf" in text


def test_failed_download_does_not_expose_implicit_recovery_urls_to_planner():
    h = SessionHistory()
    step = _make_step(1, "download_pdf", success=False)
    step.tool_result.error = "Downloaded content is not a PDF"
    step.tool_result.data = {
        "source_url": "https://example.test/preview.pdf",
        "suggested_download_urls": ["https://example.test/raw/report.pdf"],
    }
    h.add(step)

    text = h.format_for_llm()

    assert "failed: Downloaded content is not a PDF" in text
    assert "https://example.test/raw/report.pdf" not in text
    assert "https://example.test/preview.pdf" in text


def test_pdf_figure_caption_is_not_cut_by_generic_preview_limit():
    h = SessionHistory()
    caption = "Figure 1: " + "architecture detail " * 35
    step = _make_step(1, "pdf_get_figure_info")
    step.tool_result.data = {
        "path": "/a/long/output/path/report.pdf",
        "figure_number": 1,
        "caption": caption,
    }
    h.add(step)

    text = h.format_for_llm()

    assert caption in text
    assert not text.endswith("...")


def test_pdf_figure_history_keeps_local_fast_path_audit_metadata():
    h = SessionHistory()
    step = _make_step(1, "pdf_analyze_figure")
    step.tool_result.data = {
        "found": True,
        "figure_number": "1",
        "caption": "Figure 1: Architecture.",
        "local_figure_fast_path": {
            "used": True,
            "duration_seconds": 0.17,
            "confidence": 0.9946,
            "visual_kind": "vector",
        },
        "raw_document": "unneeded " * 100,
    }
    h.add(step)

    text = h.format_for_llm()

    assert '"used": true' in text
    assert '"confidence": 0.9946' in text
    assert '"visual_kind": "vector"' in text
    assert "raw_document" not in text


def test_pdf_qa_history_keeps_evidence_and_drops_verbose_noise():
    h = SessionHistory()
    step = _make_step(1, "pdf_qa")
    step.tool_result.data = {
        "question": "What does Figure 1 show?",
        "context": "Figure 1 shows the architecture.",
        "found_figures": [
            {"figure_number": "1", "caption": "Architecture", "path": "/tmp/f1.png"},
            {"figure_number": "2", "caption": "Training", "path": "/tmp/f2.png"},
            {"figure_number": "3", "caption": "Results", "path": "/tmp/f3.png"},
            {"figure_number": "4", "caption": "Noise", "path": "/tmp/f4.png"},
        ],
        "raw_document": "unneeded " * 2000,
    }
    h.add(step)

    text = h.format_for_llm()

    assert "Figure 1 shows the architecture" in text
    assert "Architecture" in text
    assert "Noise" not in text
    assert "raw_document" not in text


def test_pdf_parse_history_keeps_numbered_figures_without_verbose_content():
    h = SessionHistory()
    step = _make_step(1, "pdf_parse")
    step.tool_result.data = {
        "markdown": "full document " * 2000,
        "markdown_path": "/run/pdf/parsed.md",
        "image_count": 1,
        "images": [
            {
                "path": "/run/pdf/images/figure-1.jpg",
                "page": 2,
                "caption": "Figure 1: Architecture overview",
                "figure_number": "1",
            }
        ],
        "tables": [{"caption": "Table 1", "html_body": "<table>huge</table>"}],
    }
    h.add(step)

    text = h.format_for_llm()

    assert "Figure 1: Architecture overview" in text
    assert "/run/pdf/parsed.md" in text
    assert "full document" not in text
    assert "html_body" not in text
