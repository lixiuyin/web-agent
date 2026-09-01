"""Tests for repeated real-model benchmark aggregation."""

from pathlib import Path

import pytest
from benchmarks.studies.controlled_web_matrix import _publish_matrix, aggregate_reports


def test_aggregate_reports_uses_task_level_counts() -> None:
    reports = [
        {
            "metadata": {
                "agent_source_sha256": "a" * 64,
                "benchmark_source_sha256": "b" * 64,
            },
            "tasks": [
                {
                    "passed": True,
                    "agent_reported_success": True,
                    "action_count": 3,
                    "failed_action_count": 0,
                    "answer_assertion_count": 2,
                    "answer_assertion_passed": 2,
                    "planner_tokens": 100,
                },
                {
                    "passed": False,
                    "agent_reported_success": True,
                    "action_count": 1,
                    "failed_action_count": 1,
                    "answer_assertion_count": 2,
                    "answer_assertion_passed": 1,
                    "planner_tokens": 50,
                },
            ],
        }
    ]

    summary = aggregate_reports(reports)

    assert summary["success_rate"] == 0.5
    assert summary["false_completion_rate"] == 0.5
    assert summary["action_validity_rate"] == 0.75
    assert summary["answer_grounding_rate"] == 0.75
    assert summary["total_planner_tokens"] == 150
    assert summary["agent_source_sha256"] == "a" * 64
    assert summary["benchmark_source_sha256"] == "b" * 64


def test_aggregate_reports_rejects_mixed_benchmark_sources() -> None:
    reports = [
        {
            "metadata": {
                "agent_source_sha256": "a" * 64,
                "benchmark_source_sha256": value,
            },
            "tasks": [],
        }
        for value in ("b" * 64, "c" * 64)
    ]

    with pytest.raises(ValueError, match="benchmark_source_sha256"):
        aggregate_reports(reports)


def test_matrix_publication_preserves_batches_and_atomically_updates_latest(
    tmp_path: Path,
) -> None:
    first = _publish_matrix(tmp_path, "batch-1", '{"batch": 1}')

    assert first == tmp_path / "analysis" / "matrices" / "batch-1.json"
    assert first.read_text(encoding="utf-8") == '{"batch": 1}'
    assert (tmp_path / "matrix.json").read_text(encoding="utf-8") == '{"batch": 1}'

    second = _publish_matrix(tmp_path, "batch-2", '{"batch": 2}')

    assert second.read_text(encoding="utf-8") == '{"batch": 2}'
    assert first.read_text(encoding="utf-8") == '{"batch": 1}'
    assert (tmp_path / "matrix.json").read_text(encoding="utf-8") == '{"batch": 2}'
    assert not list(tmp_path.rglob("*.tmp"))


def test_matrix_publication_refuses_to_overwrite_an_immutable_batch(tmp_path: Path) -> None:
    snapshot = _publish_matrix(tmp_path, "batch-1", "original")

    with pytest.raises(FileExistsError, match="already exists"):
        _publish_matrix(tmp_path, "batch-1", "replacement")

    assert snapshot.read_text(encoding="utf-8") == "original"
    assert (tmp_path / "matrix.json").read_text(encoding="utf-8") == "original"
