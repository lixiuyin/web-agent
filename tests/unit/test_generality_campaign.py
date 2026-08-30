"""Current-date multi-model campaign orchestration tests."""

from __future__ import annotations

import json

from benchmarks.studies.generality_campaign import _preflight_models, parse_args


def test_campaign_preflight_is_enabled_by_default() -> None:
    args = parse_args(["--provider", "openrouter", "--models", "model-a", "model-b"])

    assert args.skip_endpoint_preflight is False


def test_explicit_offline_preflight_skip_is_audited(tmp_path) -> None:
    probes, path = _preflight_models(
        provider="offline",
        models=["model-a", "model-b"],
        output=tmp_path,
        batch_id="batch-1",
        skip=True,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [probe.status for probe in probes] == ["available", "available"]
    assert "explicitly skipped" in payload["notice"]
    assert payload["probes"][0]["endpoint_host"] == "not-checked"
