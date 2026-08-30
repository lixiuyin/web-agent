"""Real-Chromium harness baseline for the deterministic web benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from benchmarks.suites.controlled_web.general import run_benchmark


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_interaction_harness_baseline_passes_all_scenarios(tmp_path: Path) -> None:
    output = tmp_path / "web-benchmark"
    args = argparse.Namespace(
        output=output,
        mode="scripted-harness-baseline",
        tool_set="browser-only",
        headed=False,
        disable_loop_detection=False,
    )

    exit_code = await run_benchmark(args)

    assert exit_code == 0
    results = json.loads((output / "results.json").read_text())
    assert results["summary"]["task_count"] == 11
    assert results["summary"]["passed_tasks"] == 11
    assert results["summary"]["answer_grounding_rate"] == 1.0
    assert results["summary"]["false_completion_rate"] == 0
    assert results["metadata"]["mode"] == "scripted-harness-baseline"
    assert results["metadata"]["model"] == "scripted-harness-baseline"
    assert set(results["summary"]["category_success_rate"]) == {
        "dynamic",
        "form",
        "navigation",
        "recovery",
        "state_mutation",
        "account",
        "table_reasoning",
        "map_reasoning",
        "booking",
        "checkout",
    }
