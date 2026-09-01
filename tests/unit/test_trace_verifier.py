"""Anti-shortcut certificate tests for continuous run traces."""

from __future__ import annotations

import json
from pathlib import Path

from webagent.evaluation.trace_verifier import verify_trace, write_verification_certificate


def _trace() -> dict[str, object]:
    run_id = "run-1"
    return {
        "schema_version": 7,
        "run_id": run_id,
        "run_kind": "agent_e2e",
        "task": "Find a catalog using browser search",
        "status": "completed",
        "success": True,
        "evaluation": {
            "agent_source_sha256": "a" * 64,
            "mode": "search_engine_only",
            "discovery_mode": "browser-grounded",
            "direct_source_tools_enabled": False,
            "high_risk_action_policy": "deny",
            "stealth_mode": False,
            "anti_shortcut_contract": "search_engine_only_v7",
            "certificate_required": True,
            "strict_eval_mode": True,
            "search_engine_only": True,
            "browser_profile_mode": "temporary",
            "persistent_pdf_cache": False,
        },
        "steps": [
            {
                "run_id": run_id,
                "step_number": 1,
                "tool": "search",
                "success": True,
                "policy": {"decision": "allow"},
                "planner_visible_result": '{"results": [{"url": "https://example.test"}]}',
            },
            {
                "run_id": run_id,
                "step_number": 2,
                "tool": "goto",
                "success": True,
                "policy": {
                    "decision": "allow",
                    "provenance": {"source": "search_planner_visible"},
                },
                "planner_visible_result": '{"url": "https://example.test"}',
            },
            {
                "run_id": run_id,
                "step_number": 3,
                "tool": "done",
                "success": True,
                "policy": {"decision": "allow"},
                "planner_visible_result": '{"summary": "done"}',
            },
        ],
    }


def test_valid_trace_passes_and_certificate_is_hash_bound(tmp_path: Path) -> None:
    trace_path = tmp_path / "run.json"
    trace_path.write_text(json.dumps(_trace()), encoding="utf-8")

    report = verify_trace(_trace())
    certificate = write_verification_certificate(trace_path)
    persisted = json.loads(certificate.read_text(encoding="utf-8"))

    assert report["valid"] is True
    assert persisted["valid"] is True
    assert len(persisted["trace_sha256"]) == 64


def test_split_run_ids_and_hidden_url_provenance_fail() -> None:
    trace = _trace()
    steps = trace["steps"]
    assert isinstance(steps, list)
    steps[1]["run_id"] = "diagnostic-2"
    steps[1]["policy"]["provenance"]["source"] = "visible_page_link"

    report = verify_trace(trace)

    assert report["valid"] is False
    assert any("continuous run_id" in item for item in report["failures"])
    assert any("planner-visible provenance" in item for item in report["failures"])


def test_tool_augmented_or_incomplete_trace_cannot_be_benchmark() -> None:
    trace = _trace()
    trace["status"] = "failed"
    evaluation = trace["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["mode"] = "tool_augmented"
    evaluation["anti_shortcut_contract"] = None

    report = verify_trace(trace)

    assert report["valid"] is False
    assert "run did not complete" in report["failures"]
    assert any("evaluation.mode" in item for item in report["failures"])


def test_missing_source_fingerprint_invalidates_trace() -> None:
    trace = _trace()
    evaluation = trace["evaluation"]
    assert isinstance(evaluation, dict)
    evaluation["agent_source_sha256"] = "unknown"

    report = verify_trace(trace)

    assert report["valid"] is False
    assert any("agent_source_sha256" in item for item in report["failures"])


def test_latest_pdf_figure_trace_requires_complete_same_run_workflow() -> None:
    trace = _trace()
    trace["task"] = "Find the latest report PDF and interpret Figure 1"

    report = verify_trace(trace)

    assert report["valid"] is False
    assert "PDF task has no successful download_pdf" in report["failures"]
    assert "figure task has no successful figure analysis" in report["failures"]
    assert any("official_identity_search_completed" in item for item in report["failures"])


def test_unresolved_captcha_invalidates_otherwise_successful_trace() -> None:
    trace = _trace()
    trace["events"] = [
        {"type": "captcha_detected", "outcome": "blocked", "challenge_type": "recaptcha"}
    ]

    report = verify_trace(trace)

    assert report["valid"] is False
    assert "run contains an unresolved CAPTCHA/challenge" in report["failures"]
