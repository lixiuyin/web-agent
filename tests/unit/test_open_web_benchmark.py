"""Tests for dated open-web manifests and longitudinal summaries."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from benchmarks.studies.open_web_longitudinal import load_slices
from benchmarks.suites.open_web.runner import (
    append_time_slice,
    benchmark_config_evidence,
    canonical_sha256,
    load_manifest,
    require_free_space,
)

from webagent.core.config import AgentConfig

_RESEARCH_FIELDS = {
    "split",
    "task_family",
    "setting_id",
    "leakage_group",
    "target_failure_modes",
    "feedback",
    "expected_horizon",
}


def _raw_manifest(name: str) -> dict[str, object]:
    return json.loads(Path(f"benchmarks/manifests/{name}").read_text(encoding="utf-8"))


def test_repository_open_web_manifest_is_current_and_source_grounded() -> None:
    suite, tasks, digest = load_manifest(
        Path("benchmarks/manifests/open_web_smoke.json"),
        today=date(2026, 8, 29),
    )

    assert suite == "open-web-smoke-v1"
    assert len(tasks) == 3
    assert len(digest) == 64
    assert all(task.network_required and task.source_urls for task in tasks)
    assert all(any(a.kind.startswith("answer_") for a in task.assertions) for task in tasks)


def test_smoke_manifest_predeclares_validation_research_metadata() -> None:
    raw = _raw_manifest("open_web_smoke.json")
    tasks = raw["tasks"]

    assert raw["schema_version"] == 2
    assert isinstance(tasks, list)
    assert all(task.keys() >= _RESEARCH_FIELDS for task in tasks)
    assert all(task["split"] == "validation" for task in tasks)
    assert all(task["feedback"] == {"kind": "verifiable"} for task in tasks)
    assert all(task["expected_horizon"] == "short" for task in tasks)
    assert all(task["target_failure_modes"] for task in tasks)


def test_general_manifest_has_thirty_tasks_across_ten_domains() -> None:
    suite, tasks, _digest = load_manifest(
        Path("benchmarks/manifests/open_web_general.json"),
        today=date(2026, 8, 29),
    )
    domains = {task.source_urls[0].split("/", 3)[2] for task in tasks}
    discovery_tasks = [task for task in tasks if task.discovery_required]

    assert suite == "open-web-general-v2"
    assert len(tasks) == 30
    assert len(domains) == 10
    assert len(discovery_tasks) == 10
    assert all(task.start_url == "about:blank" for task in discovery_tasks)
    assert all(task.entry_mode == "search" for task in discovery_tasks)
    assert all(task.scenario == "search_discovery" for task in discovery_tasks)
    assert all(
        any(assertion.kind == "certificate_valid" for assertion in task.assertions)
        for task in discovery_tasks
    )
    assert all(task.network_required and len(task.source_urls) == 1 for task in tasks)
    assert all(
        {"answer_contains", "history_url_observed"}
        <= {assertion.kind for assertion in task.assertions}
        for task in tasks
    )


def test_general_manifest_predeclares_leakage_safe_transfer_splits() -> None:
    raw = _raw_manifest("open_web_general.json")
    raw_tasks = raw["tasks"]
    _suite, tasks, _digest = load_manifest(
        Path("benchmarks/manifests/open_web_general.json"),
        today=date(2026, 8, 29),
    )

    assert isinstance(raw_tasks, list)
    assert all(task.keys() >= _RESEARCH_FIELDS for task in raw_tasks)
    assert Counter(task.split for task in tasks) == {
        "development": 18,
        "held_out_task": 6,
        "held_out_setting": 6,
    }
    assert all(task.feedback.kind == "verifiable" for task in tasks)
    assert all(task.expected_horizon in {"short", "medium"} for task in tasks)
    assert all(task.target_failure_modes for task in tasks)

    domain_splits: defaultdict[str, set[str]] = defaultdict(set)
    leakage_splits: defaultdict[str, set[str]] = defaultdict(set)
    for task in tasks:
        domain_splits[urlsplit(task.source_urls[0]).hostname or ""].add(task.split)
        leakage_splits[task.leakage_group or ""].add(task.split)

    assert len(domain_splits) == 10
    assert all(len(splits) == 1 for splits in domain_splits.values())
    assert all(len(splits) == 1 for splits in leakage_splits.values())

    development_settings = {task.setting_id for task in tasks if task.split == "development"}
    held_out_task_settings = {task.setting_id for task in tasks if task.split == "held_out_task"}
    held_out_setting_settings = {
        task.setting_id for task in tasks if task.split == "held_out_setting"
    }
    assert held_out_task_settings <= development_settings
    assert held_out_setting_settings.isdisjoint(development_settings)


def test_qwen_strict_manifest_targets_long_held_out_document_workflow() -> None:
    suite, tasks, _digest = load_manifest(
        Path("benchmarks/manifests/qwen_strict_search.json"),
        today=date(2026, 8, 30),
    )

    assert suite == "qwen-strict-search-v1"
    assert len(tasks) == 1
    task = tasks[0]
    assert task.split == "held_out_setting"
    assert task.expected_horizon == "long"
    assert task.feedback.kind == "verifiable"
    assert {
        "search_recency",
        "source_grounding",
        "document_download",
        "figure_detection",
        "figure_interpretation",
    } <= set(task.target_failure_modes)

    raw_task = _raw_manifest("qwen_strict_search.json")["tasks"][0]
    assert raw_task.keys() >= _RESEARCH_FIELDS


def test_stale_manifest_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "stale.json"
    manifest.write_text(
        json.dumps(
            {
                "suite": "stale",
                "tasks": [
                    {
                        "id": "old",
                        "category": "open",
                        "goal": "read",
                        "start_url": "https://example.com",
                        "assertions": [
                            {"kind": "answer_contains", "expected": "https://example.com"},
                            {
                                "kind": "history_url_observed",
                                "expected": "https://example.com",
                            },
                        ],
                        "source_urls": ["https://example.com"],
                        "snapshot_id": "old-2025",
                        "network_required": True,
                        "valid_from": "2025-01-01",
                        "valid_until": "2025-12-31",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="validity window"):
        load_manifest(manifest, today=date(2026, 8, 29))


def test_time_slice_history_appends_without_overwriting(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"suite": "open", "tasks": [{"id": "task-1"}]}), encoding="utf-8"
    )
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    benchmark_config = {"manifest_sha256": manifest_hash, "stealth_mode": False}
    report = {
        "created_at": "2026-08-29T00:00:00Z",
        "suite": "open",
        "metadata": {
            "run_id": "run-1",
            "model": "model-a",
            "manifest": str(manifest.resolve()),
            "manifest_sha256": manifest_hash,
            "benchmark_config": benchmark_config,
            "benchmark_config_sha256": canonical_sha256(benchmark_config),
            "task_ids_sha256": canonical_sha256(["task-1"]),
            "discovery_mode": "browser-grounded",
            "stealth_mode": False,
        },
        "summary": {
            "task_count": 1,
            "success_rate": 2 / 3,
            "answer_grounding_rate": 0.75,
            "false_completion_rate": 0.1,
        },
        "tasks": [{"task_id": "task-1"}],
    }
    target = tmp_path / "history.jsonl"
    results = tmp_path / "results.json"
    results.write_text(json.dumps(report), encoding="utf-8")

    append_time_slice(target, report, manifest_hash, report_path=results)
    append_time_slice(target, report, manifest_hash, report_path=results)

    records = load_slices([target])
    assert len(records) == 2
    assert records[0]["answer_grounding_rate"] == 0.75
    assert records[0]["model"] == "model-a"
    assert records[0]["provider"] == "unknown"
    assert records[0]["study_manifest_sha256"] == "unknown"
    assert records[0]["evidence_kind"] == "local-report-bound-v1"
    assert records[0]["agent_source_sha256"] == "unknown"


def test_benchmark_config_hash_input_uses_final_effective_agent_config(tmp_path: Path) -> None:
    stealth = AgentConfig(output_dir=tmp_path, stealth_mode=True, browser_timeout=4567)
    plain = AgentConfig(output_dir=tmp_path, stealth_mode=False, browser_timeout=4567)

    stealth_evidence = benchmark_config_evidence(
        stealth,
        manifest_sha256="m" * 64,
        max_steps_per_task=8,
        discovery_task_count=10,
        provider="openrouter",
        study_manifest_sha256="s" * 64,
    )
    plain_evidence = benchmark_config_evidence(
        plain,
        manifest_sha256="m" * 64,
        max_steps_per_task=8,
        discovery_task_count=10,
        provider="openrouter",
        study_manifest_sha256="s" * 64,
    )

    assert stealth_evidence["stealth_mode"] is True
    assert stealth_evidence["browser_timeout"] == 4567
    assert plain_evidence["provider"] == "openrouter"
    assert plain_evidence["study_manifest_sha256"] == "s" * 64
    assert canonical_sha256(stealth_evidence) != canonical_sha256(plain_evidence)


def test_free_space_preflight_fails_before_browser_start(tmp_path: Path, monkeypatch) -> None:
    class _Usage:
        free = 100

    monkeypatch.setattr(
        "benchmarks.suites.open_web.runner.shutil.disk_usage", lambda _path: _Usage()
    )

    with pytest.raises(RuntimeError, match="insufficient free space"):
        require_free_space(tmp_path, minimum_bytes=101)

    require_free_space(tmp_path, minimum_bytes=100)
