"""Deterministic strict-mode workflow through the real browser and agent loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout


class _EvidencePlanner:
    """Script only workflow shape; all report facts come back from the executor."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_call_metadata: dict[str, object] = {}

    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        return "fixture analysis"

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall:
        self.calls += 1
        self.last_call_metadata = {
            "response_length": 80,
            "finish_reason": "stop",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }
        if self.calls == 1:
            return ToolCall(
                tool_name="official_report_search",
                parameters={"subject": "Aurora", "official_owner": "AcmeAI"},
            )
        if self.calls == 2:
            assert "Aurora-Next" in history_text
            return ToolCall(
                tool_name="download_pdf",
                parameters={"url": "https://fixtures.invalid/aurora-next.pdf"},
            )
        if self.calls == 3:
            assert "aurora-next.pdf" in history_text
            return ToolCall(
                tool_name="pdf_analyze_figure",
                parameters={"path": "aurora-next.pdf", "figure_number_or_caption": "1"},
            )
        assert "Figure 1" in history_text
        return ToolCall(
            tool_name="done",
            parameters={
                "summary": "Aurora-Next is the newest verified first-party report; Figure 1 "
                "summarizes its modular architecture.",
            },
        )


class _FixtureExecutor:
    def __init__(self, artifacts_dir: Path) -> None:
        self.artifacts_dir = artifacts_dir
        self.calls: list[str] = []

    def get_tool_descriptions(self) -> str:
        return "official_report_search, download_pdf, pdf_analyze_figure, done"

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        self.calls.append(tool_call.tool_name)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if tool_call.tool_name == "official_report_search":
            return ToolResult(
                success=True,
                tool_name=tool_call.tool_name,
                data={
                    "subject": "Aurora",
                    "official_owner": "AcmeAI",
                    "verified_first_party_candidates": [
                        {
                            "source": "github",
                            "title": "AcmeAI/Aurora-Next",
                            "date": "2030-04-12T08:00:00Z",
                            "pdf_url": "https://fixtures.invalid/aurora-next.pdf",
                            "first_party": True,
                        }
                    ],
                    "source_status": {"arxiv": "ok", "github": "ok"},
                },
            )
        if tool_call.tool_name == "download_pdf":
            path = self.artifacts_dir / "aurora-next.pdf"
            path.write_bytes(b"%PDF-1.4 fixture")
            return ToolResult(
                success=True,
                tool_name=tool_call.tool_name,
                data={"path": str(path), "source_url": tool_call.parameters["url"]},
            )
        if tool_call.tool_name == "pdf_analyze_figure":
            path = self.artifacts_dir / "figure-1.png"
            Image.new("RGB", (16, 16), "navy").save(path)
            return ToolResult(
                success=True,
                tool_name=tool_call.tool_name,
                data={
                    "found": True,
                    "figure_number": "1",
                    "caption": "Figure 1: Aurora-Next modular architecture.",
                    "image_path": str(path),
                    "vision_analysis": "The blocks show a modular data flow.",
                },
            )
        return ToolResult(
            success=True,
            tool_name="done",
            data={"summary": tool_call.parameters["summary"]},
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_strict_eval_is_isolated_traceable_and_answer_agnostic(tmp_path: Path) -> None:
    output_dir = tmp_path / "strict-output"
    config = AgentConfig(
        output_dir=output_dir,
        strict_eval_mode=True,
        persistent_pdf_cache=False,
        browser_profile_mode="temporary",
        browser_headless=True,
        use_cdp=False,
        captcha_pause=False,
        post_action_wait_ms=0,
        max_steps=6,
        task_timeout=30,
    )
    planner = _EvidencePlanner()
    executor = _FixtureExecutor(output_dir / "artifacts")
    browser = BrowserController(headless=True, temporary_profile=True, slow_mo=1)
    await browser.start()
    profile_dir = Path(browser.user_data_dir)

    try:
        agent = WebAgent(planner, browser, executor, config=config)
        result = await agent.run(
            "Find Aurora's newest official technical-report PDF and interpret Figure 1"
        )

        assert result.status == "completed"
        assert executor.calls == [
            "official_report_search",
            "download_pdf",
            "pdf_analyze_figure",
            "done",
        ]
        assert len(result.planner_attempts) == 4
        assert all(attempt.success for attempt in result.planner_attempts)
        layout = RunLayout.from_root(output_dir)
        trace = json.loads(layout.trace_path.read_text())
        source_fingerprint = trace["evaluation"].pop("agent_source_sha256")
        assert isinstance(source_fingerprint, str) and len(source_fingerprint) == 64
        assert trace["evaluation"] == {
            "mode": "search_engine_only",
            "discovery_mode": "browser-grounded",
            "direct_source_tools_enabled": False,
            "high_risk_action_policy": "deny",
            "stealth_mode": False,
            "anti_shortcut_contract": "search_engine_only_v8",
            "certificate_required": True,
            "strict_eval_mode": True,
            "search_engine_only": True,
            "browser_profile_mode": "temporary",
            "persistent_pdf_cache": False,
        }
        assert [step["tool"] for step in trace["steps"]] == executor.calls
        assert "Qwen" not in json.dumps(trace)
        verification = json.loads(layout.verification_path.read_text())
        assert verification["valid"] is False
        assert any("forbidden discovery tools" in item for item in verification["failures"])
        assert profile_dir.is_dir()
    finally:
        await browser.close()

    assert not profile_dir.exists()
