"""Mechanical integrity checks for one continuous search-only agent run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from webagent.evaluation.trace_schema import (
    RUN_TRACE_SCHEMA_VERSION,
    TraceSchemaError,
    migrate_trace_to_v8,
)
from webagent.tools.exposure import DIRECT_SOURCE_DISCOVERY_TOOLS

_FORBIDDEN_DISCOVERY_TOOLS = DIRECT_SOURCE_DISCOVERY_TOOLS
_VISIBLE_PROVENANCE_SOURCES = {
    "planner_state_current_url",
    "search_planner_visible",
    "get_all_links_planner_visible",
    "get_attribute_planner_visible",
    "get_search_results_planner_visible",
    "get_url_planner_visible",
    "inspect_download_links_planner_visible",
}


def verify_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Validate a supported trace version, then verify anti-shortcut invariants."""
    source_schema_version = trace.get("schema_version")
    try:
        normalized = migrate_trace_to_v8(trace)
    except TraceSchemaError as exc:
        return {
            "valid": False,
            "run_id": trace.get("run_id"),
            "source_schema_version": source_schema_version,
            "verified_schema_version": None,
            "checks": {
                "schema_supported": False,
                "single_continuous_run": False,
                "search_engine_only": False,
                "first_successful_action_is_search": False,
                "planner_visible_url_provenance": False,
                "producer_source_bound": False,
            },
            "failures": [str(exc)],
            "warnings": [],
        }

    report = _verify_v8(normalized)
    report["source_schema_version"] = source_schema_version
    report["verified_schema_version"] = RUN_TRACE_SCHEMA_VERSION
    checks = report["checks"]
    if isinstance(checks, dict):
        checks["schema_supported"] = True
    if source_schema_version == 7:
        warnings = report["warnings"]
        if isinstance(warnings, list):
            warnings.insert(0, "trace schema v7 was migrated to v8 before verification")
    return report


def _verify_v8(trace: dict[str, Any]) -> dict[str, Any]:
    """Verify the normalized v8 anti-shortcut contract."""
    failures: list[str] = []
    warnings: list[str] = []
    evaluation = _evaluation(trace, failures)
    raw_steps = trace.get("steps")
    run_id = trace.get("run_id")
    task = str(trace.get("task", "")).casefold()

    _require(
        trace.get("schema_version") == RUN_TRACE_SCHEMA_VERSION,
        f"trace schema is not v{RUN_TRACE_SCHEMA_VERSION}",
        failures,
    )
    _require(trace.get("run_kind") == "agent_e2e", "trace is not an agent_e2e run", failures)
    _require(isinstance(run_id, str) and bool(run_id), "run_id is missing", failures)
    _require(trace.get("status") == "completed", "run did not complete", failures)
    _require(trace.get("success") is True, "run success flag is false", failures)
    _require(isinstance(raw_steps, list) and bool(raw_steps), "run has no steps", failures)
    _check_evaluation(evaluation, failures)
    _check_producer_binding(trace, evaluation, failures)

    valid_steps = _valid_steps(raw_steps, run_id, failures)
    successful = [step for step in valid_steps if step.get("success") is True]
    _check_workflow(task, successful, failures)
    _check_shortcuts(valid_steps, successful, failures, warnings)
    _check_step_policies(valid_steps, failures)
    _check_latest_evidence(task, valid_steps, failures)
    _check_runtime_events(trace.get("events"), failures)
    _check_continuation(trace, failures)

    return {
        "valid": not failures,
        "run_id": run_id,
        "checks": {
            "single_continuous_run": bool(run_id)
            and all(step.get("run_id") == run_id for step in valid_steps),
            "search_engine_only": evaluation.get("search_engine_only") is True,
            "first_successful_action_is_search": bool(successful)
            and successful[0].get("tool") == "search",
            "planner_visible_url_provenance": not any(
                "planner-visible provenance" in failure for failure in failures
            ),
            "producer_source_bound": not any(
                "producer source SHA-256" in failure for failure in failures
            ),
        },
        "failures": failures,
        "warnings": warnings,
    }


def _evaluation(trace: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    value = trace.get("evaluation")
    if isinstance(value, dict):
        return value
    failures.append("evaluation metadata is missing")
    return {}


def _check_evaluation(evaluation: dict[str, Any], failures: list[str]) -> None:
    expected = {
        "mode": "search_engine_only",
        "anti_shortcut_contract": "search_engine_only_v8",
        "certificate_required": True,
        "strict_eval_mode": True,
        "search_engine_only": True,
        "discovery_mode": "browser-grounded",
        "direct_source_tools_enabled": False,
        "high_risk_action_policy": "deny",
        "stealth_mode": False,
        "browser_profile_mode": "temporary",
        "persistent_pdf_cache": False,
    }
    for key, expected_value in expected.items():
        _require(
            evaluation.get(key) == expected_value,
            f"evaluation.{key} must be {expected_value!r}",
            failures,
        )
    _require(
        isinstance(evaluation.get("agent_source_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", evaluation["agent_source_sha256"]) is not None,
        "evaluation.agent_source_sha256 must be a lowercase SHA-256 digest",
        failures,
    )


def _check_producer_binding(
    trace: dict[str, Any], evaluation: dict[str, Any], failures: list[str]
) -> None:
    producer = trace.get("producer")
    producer_sha = producer.get("source_sha256") if isinstance(producer, dict) else None
    _require(
        producer_sha == evaluation.get("agent_source_sha256"),
        "producer source SHA-256 does not match evaluation source SHA-256",
        failures,
    )


def _valid_steps(raw_steps: Any, run_id: Any, failures: list[str]) -> list[dict[str, Any]]:
    steps = (
        [step for step in raw_steps if isinstance(step, dict)]
        if isinstance(raw_steps, list)
        else []
    )
    _require(len(steps) == len(raw_steps or []), "one or more steps are malformed", failures)
    _require(
        all(step.get("run_id") == run_id for step in steps),
        "steps do not belong to one continuous run_id",
        failures,
    )
    return steps


def _check_workflow(task: str, successful: list[dict[str, Any]], failures: list[str]) -> None:
    _require(
        bool(successful) and successful[0].get("tool") == "search",
        "first successful action was not search",
        failures,
    )
    tools = [str(step.get("tool", "")).casefold() for step in successful]
    _require("search" in tools, "no successful browser search", failures)
    _require("done" in tools, "no successful done action", failures)
    if "pdf" in task:
        _require("download_pdf" in tools, "PDF task has no successful download_pdf", failures)
    if "figure" in task or "图" in task:
        _require(
            "pdf_analyze_figure" in tools or "pdf_get_figure_info" in tools,
            "figure task has no successful figure analysis",
            failures,
        )


def _check_shortcuts(
    valid_steps: list[dict[str, Any]],
    successful: list[dict[str, Any]],
    failures: list[str],
    warnings: list[str],
) -> None:
    tools = [str(step.get("tool", "")).casefold() for step in successful]
    forbidden_successes = sorted(set(tools) & _FORBIDDEN_DISCOVERY_TOOLS)
    _require(
        not forbidden_successes,
        f"forbidden discovery tools succeeded: {forbidden_successes}",
        failures,
    )
    blocked = [
        step
        for step in valid_steps
        if str(step.get("tool", "")).casefold() in _FORBIDDEN_DISCOVERY_TOOLS
    ]
    if blocked:
        warnings.append(f"policy blocked {len(blocked)} direct-source shortcut attempt(s)")


def _check_step_policies(valid_steps: list[dict[str, Any]], failures: list[str]) -> None:
    for step in valid_steps:
        policy = step.get("policy")
        if not isinstance(policy, dict):
            failures.append(f"step {step.get('step_number')} has no policy audit")
            continue
        if step.get("success") is True and policy.get("decision") != "allow":
            failures.append(f"successful step {step.get('step_number')} was not policy-allowed")
        if (
            str(step.get("tool", "")).casefold() in {"goto", "download_pdf"}
            and policy.get("decision") == "allow"
        ):
            provenance = policy.get("provenance")
            source = provenance.get("source") if isinstance(provenance, dict) else None
            if source not in _VISIBLE_PROVENANCE_SOURCES:
                failures.append(
                    f"step {step.get('step_number')} URL lacks planner-visible provenance"
                )
        if "suggested_download_urls" in str(step.get("planner_visible_result", "")):
            failures.append(
                f"step {step.get('step_number')} used implicit download URL suggestions"
            )


def _check_latest_evidence(
    task: str, valid_steps: list[dict[str, Any]], failures: list[str]
) -> None:
    latest_task = any(term in task for term in ("latest", "newest", "most recent", "最新", "最近"))
    if not latest_task:
        return
    terminal_policy: dict[str, Any] = next(
        (
            step.get("policy", {})
            for step in reversed(valid_steps)
            if step.get("tool") == "done" and step.get("success") is True
        ),
        {},
    )
    for key in (
        "broad_current_year_search_completed",
        "release_landscape_search_completed",
        "official_identity_search_completed",
        "official_scope_search_completed",
        "newer_version_leads_resolved",
    ):
        _require(terminal_policy.get(key) is True, f"latest-task evidence missing: {key}", failures)


def _check_runtime_events(value: Any, failures: list[str]) -> None:
    events = value if isinstance(value, list) else []
    unresolved = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("type") == "captcha_detected"
        and event.get("outcome") != "resolved_by_human"
    ]
    _require(not unresolved, "run contains an unresolved CAPTCHA/challenge", failures)


def _check_continuation(trace: dict[str, Any], failures: list[str]) -> None:
    _require(
        trace.get("resumed_from_checkpoint") is False,
        "strict trace was resumed from a checkpoint",
        failures,
    )
    _require(trace.get("resume_count") == 0, "strict trace has a nonzero resume count", failures)


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def write_verification_certificate(trace_path: Path) -> Path:
    """Write a hash-bound verification report beside the supplied trace JSON."""
    raw = trace_path.read_bytes()
    trace = json.loads(raw)
    report = verify_trace(trace)
    report["certificate_schema_version"] = 1
    report["trace_sha256"] = hashlib.sha256(raw).hexdigest()
    report["trace_path"] = trace_path.name
    certificate_path = trace_path.with_name("verification.json")
    temporary = certificate_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(certificate_path)
    except OSError:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    return certificate_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify one webagent trajectory trace")
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    certificate = write_verification_certificate(args.trace)
    report = json.loads(certificate.read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()


__all__ = ["verify_trace", "write_verification_certificate"]
