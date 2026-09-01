"""Current-date multi-model campaign orchestration tests."""

from __future__ import annotations

import json

import pytest
from benchmarks.studies.generality_campaign import (
    _ensure_campaign_contract,
    _preflight_models,
    _run_component,
    _write_campaign_state,
    parse_args,
)


def test_campaign_preflight_is_enabled_by_default() -> None:
    args = parse_args(["--provider", "openrouter", "--models", "model-a", "model-b"])

    assert args.skip_endpoint_preflight is False
    assert args.shards == 1
    assert args.model_order == "rotate-by-date"
    assert args.require_new_date is True
    assert args.open_study_name == "open-web"
    assert args.output.as_posix() == "outputs/campaigns/generality-campaign-v2"


def test_explicit_offline_preflight_skip_is_audited(tmp_path) -> None:
    probes, path = _preflight_models(
        provider="offline",
        models=["model-a", "model-b"],
        path=tmp_path / "batches" / "2026-09-01" / "batch-1" / "evidence" / "endpoint-probes.json",
        skip=True,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [probe.status for probe in probes] == ["available", "available"]
    assert "explicitly skipped" in payload["notice"]
    assert payload["probes"][0]["endpoint_host"] == "not-checked"


def test_campaign_contract_is_immutable_across_batches(tmp_path) -> None:
    path = tmp_path / "campaign.json"
    contract = {"format": "webagent-generality-campaign", "schema_version": 1}

    _ensure_campaign_contract(path, contract)
    _ensure_campaign_contract(path, contract)

    assert json.loads(path.read_text(encoding="utf-8")) == contract
    with pytest.raises(RuntimeError, match="differs from the immutable"):
        _ensure_campaign_contract(path, {**contract, "provider": "changed"})


def test_campaign_state_is_atomic_and_records_failure(tmp_path) -> None:
    path = tmp_path / "batches" / "2026-09-01" / "batch-1" / "batch.json"
    base = {"schema_version": 2, "batch_id": "batch"}

    _write_campaign_state(path, base, status="running", component="open-web")
    running = json.loads(path.read_text(encoding="utf-8"))
    assert running["status"] == "running"
    assert running["active_component"] == "open-web"

    _write_campaign_state(
        path,
        base,
        status="failed",
        component="open-web",
        error="RuntimeError: failed",
    )
    failed = json.loads(path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError: failed"
    assert not path.with_suffix(".json.tmp").exists()


def test_campaign_component_records_operator_abort(tmp_path, monkeypatch) -> None:
    path = tmp_path / "batches" / "2026-09-01" / "batch-1" / "batch.json"

    def interrupt(*args, **kwargs):
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr("benchmarks.studies.generality_campaign._run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _run_component(
            ["ignored"],
            log_path=tmp_path / "component.log",
            expected_report=None,
            campaign_path=path,
            campaign_base={"schema_version": 2, "batch_id": "batch"},
            component="open-web",
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert payload["active_component"] == "open-web"
