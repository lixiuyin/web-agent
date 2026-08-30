"""Versioned trace schema, migration, and packaged-schema tests."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from webagent.evaluation.trace_schema import (
    RUN_TRACE_SCHEMA_ID,
    RUN_TRACE_SCHEMA_VERSION,
    TraceSchemaError,
    build_run_trace_v8,
    migrate_trace_to_v8,
)
from webagent.evaluation.trace_verifier import verify_trace, write_verification_certificate


def _evaluation(contract: str = "search_engine_only_v7") -> dict[str, object]:
    return {
        "agent_source_sha256": "a" * 64,
        "mode": "search_engine_only",
        "discovery_mode": "browser-grounded",
        "direct_source_tools_enabled": False,
        "high_risk_action_policy": "deny",
        "stealth_mode": False,
        "anti_shortcut_contract": contract,
        "certificate_required": True,
        "strict_eval_mode": True,
        "search_engine_only": True,
        "browser_profile_mode": "temporary",
        "persistent_pdf_cache": False,
    }


def _legacy_trace() -> dict[str, object]:
    return {
        "schema_version": 7,
        "run_id": "run-1",
        "run_kind": "agent_e2e",
        "task": "Find a catalog using browser search",
        "status": "completed",
        "success": True,
        "evaluation": _evaluation(),
        "steps": [
            {
                "step_number": 1,
                "run_id": "run-1",
                "timestamp": "2026-08-30T01:02:03+00:00",
                "tool": "search",
                "success": True,
                "policy": {"decision": "allow"},
                "planner_visible_result": "{}",
            },
            {
                "step_number": 2,
                "run_id": "run-1",
                "tool": "done",
                "success": True,
                "policy": {"decision": "allow"},
                "planner_visible_result": "{}",
            },
        ],
    }


def test_v7_migration_is_deterministic_and_validates_against_packaged_schema() -> None:
    migrated = migrate_trace_to_v8(_legacy_trace())
    repeated = migrate_trace_to_v8(_legacy_trace())

    assert migrated == repeated
    assert migrated["schema_version"] == RUN_TRACE_SCHEMA_VERSION
    assert migrated["$schema"] == RUN_TRACE_SCHEMA_ID
    assert migrated["created_at"] == "2026-08-30T01:02:03+00:00"
    assert migrated["evaluation"]["anti_shortcut_contract"] == "search_engine_only_v8"
    assert migrated["resume_count"] == 0
    assert migrated["checkpoint_schema_version"] is None
    assert migrated["resumed_from_checkpoint"] is False

    schema = json.loads(
        resources.files("webagent.schemas")
        .joinpath("run-trace-v8.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(migrated)


def test_new_trace_builder_owns_schema_and_producer_fields() -> None:
    payload = _legacy_trace()
    payload.pop("schema_version")

    trace = build_run_trace_v8(payload)

    assert trace["schema_version"] == 8
    assert trace["$schema"] == RUN_TRACE_SCHEMA_ID
    assert trace["producer"]["name"] == "lixiuyin-webagent"
    assert len(trace["producer"]["source_sha256"]) == 64
    assert trace["evaluation"]["agent_source_sha256"] == trace["producer"]["source_sha256"]


@pytest.mark.parametrize("version", [None, 6, 9, "8"])
def test_unknown_or_mistyped_schema_versions_are_rejected(version: object) -> None:
    trace = _legacy_trace()
    trace["schema_version"] = version

    with pytest.raises(TraceSchemaError, match="unsupported trace schema version"):
        migrate_trace_to_v8(trace)

    report = verify_trace(trace)
    assert report["valid"] is False
    assert report["verified_schema_version"] is None
    assert report["checks"]["schema_supported"] is False


def test_verifier_dispatches_v7_through_v8_and_versions_certificate(tmp_path: Path) -> None:
    trace = _legacy_trace()
    report = verify_trace(trace)

    assert report["valid"] is True
    assert report["source_schema_version"] == 7
    assert report["verified_schema_version"] == 8
    assert report["checks"]["schema_supported"] is True
    assert "migrated to v8" in report["warnings"][0]

    trace_path = tmp_path / "run.json"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    certificate = json.loads(write_verification_certificate(trace_path).read_text(encoding="utf-8"))
    assert certificate["certificate_schema_version"] == 1
    assert certificate["source_schema_version"] == 7
    assert certificate["verified_schema_version"] == 8


def test_malformed_v8_is_reported_instead_of_partially_verified() -> None:
    trace = migrate_trace_to_v8(_legacy_trace())
    trace["unexpected"] = True

    report = verify_trace(trace)

    assert report["valid"] is False
    assert report["verified_schema_version"] is None
    assert "invalid v8 trace" in report["failures"][0]


def test_strict_verifier_rejects_a_resumed_checkpoint_trace() -> None:
    trace = migrate_trace_to_v8(_legacy_trace())
    trace.update(
        {
            "resume_count": 1,
            "checkpoint_schema_version": 1,
            "resumed_from_checkpoint": True,
        }
    )

    report = verify_trace(trace)

    assert report["valid"] is False
    assert "strict trace was resumed from a checkpoint" in report["failures"]
    assert "strict trace has a nonzero resume count" in report["failures"]


def test_verifier_rejects_mismatched_producer_and_evaluation_source_hashes() -> None:
    trace = migrate_trace_to_v8(_legacy_trace())
    trace["producer"]["source_sha256"] = "b" * 64

    report = verify_trace(trace)

    assert report["valid"] is False
    assert report["checks"]["producer_source_bound"] is False
    assert "producer source SHA-256 does not match evaluation source SHA-256" in report["failures"]
