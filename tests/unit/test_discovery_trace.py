"""Trace labels must distinguish browser-grounded and hybrid discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webagent.agent.loop import _persist_run_trace
from webagent.core.config import AgentConfig
from webagent.core.models import AgentResult
from webagent.evaluation.artifacts import RunLayout


@pytest.mark.parametrize(
    ("discovery_mode", "trace_mode", "direct_sources"),
    [
        ("browser-grounded", "browser_grounded", False),
        ("hybrid", "hybrid_api_augmented", True),
    ],
)
def test_non_strict_trace_records_discovery_exposure(
    tmp_path: Path,
    discovery_mode: str,
    trace_mode: str,
    direct_sources: bool,
) -> None:
    layout = RunLayout.from_root(tmp_path)
    layout.trajectory_dir.mkdir()
    config = AgentConfig(_env_file=None, discovery_mode=discovery_mode)
    result = AgentResult(
        success=True,
        status="completed",
        steps_taken=0,
        total_duration=0.1,
    )

    _persist_run_trace(tmp_path, "trace discovery mode", result, config)

    trace = json.loads(layout.trace_path.read_text())
    assert trace["schema_version"] == 8
    assert trace["$schema"].endswith("run-trace-v8.schema.json")
    assert trace["producer"]["name"] == "lixiuyin-webagent"
    assert trace["evaluation"]["mode"] == trace_mode
    assert trace["evaluation"]["discovery_mode"] == discovery_mode
    assert trace["evaluation"]["direct_source_tools_enabled"] is direct_sources
