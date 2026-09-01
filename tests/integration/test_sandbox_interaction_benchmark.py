"""Real-Chromium harness baseline for stateful multi-origin sandbox workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from benchmarks.suites.controlled_web.sandbox import run_benchmark


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sandbox_harness_baseline_covers_all_complex_scenarios(
    tmp_path: Path,
) -> None:
    output = tmp_path / "sandbox-benchmark"
    args = argparse.Namespace(
        output=output,
        mode="scripted-harness-baseline",
        model=None,
        headed=False,
        max_steps_per_task=12,
    )

    exit_code = await run_benchmark(args)

    assert exit_code == 0
    results = json.loads((output / "results.json").read_text())
    assert results["summary"]["task_count"] == 5
    assert results["summary"]["passed_tasks"] == 5
    assert results["summary"]["false_completion_rate"] == 0
    assert results["summary"]["action_validity_rate"] == 1.0
    assert set(results["summary"]["category_success_rate"]) == {
        "spa",
        "authentication",
        "cross_origin_form",
        "file_workflow",
        "sandbox_transaction",
    }
    assert results["metadata"]["origin_count"] == 2
    assert results["metadata"]["mode"] == "scripted-harness-baseline"
    assert results["metadata"]["model"] == "scripted-harness-baseline"
    assert results["metadata"]["public_mutations_allowed"] is False
    assert (
        output
        / "runs"
        / "download_upload_handoff"
        / "artifacts"
        / "downloads"
        / "sandbox-payload.txt"
    ).is_file()
