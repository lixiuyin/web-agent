"""Collect one real-date, multi-model generality and long-horizon evidence slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.core import allocate_execution_dir, default_campaign_dir, packaged_manifest_path
from benchmarks.studies.open_web_matrix import _ordered_models
from webagent.core.config import AgentConfig
from webagent.evaluation.artifacts import CampaignLayout
from webagent.evaluation.endpoints import EndpointProbe, probe_chat_endpoint
from webagent.evaluation.portfolio import load_empirical_portfolio, write_empirical_portfolio
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_campaign_dir("generality-campaign-v2"),
        help=("Exact campaign root; defaults to outputs/campaigns/generality-campaign-v2"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=packaged_manifest_path("open_web_general.json"),
    )
    parser.add_argument(
        "--shards",
        type=int,
        default=1,
        help="Open-web worker count; serial by default to avoid shared-provider rate-limit bias",
    )
    parser.add_argument("--open-max-steps", type=int, default=8)
    parser.add_argument("--open-discovery-max-steps", type=int, default=12)
    parser.add_argument(
        "--open-study-name",
        default="open-web",
        help="Explicit immutable open-web preregistration directory below suites/",
    )
    parser.add_argument("--sandbox-max-steps", type=int, default=18)
    parser.add_argument("--long-max-steps", type=int, default=100)
    parser.add_argument("--resume-at-step", type=int, default=35)
    parser.add_argument("--long-planner-max-tokens", type=int, default=1024)
    parser.add_argument(
        "--long-planner-reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    parser.add_argument("--captcha-handling", choices=("fail", "report"), default="fail")
    parser.add_argument(
        "--skip-endpoint-preflight",
        action="store_true",
        help="Skip the real minimal inference probe (intended only for offline harness tests)",
    )
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument(
        "--model-order",
        choices=("as-given", "reverse", "rotate-by-date"),
        default="rotate-by-date",
    )
    parser.add_argument(
        "--require-new-date",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args(argv)


def run_campaign(args: argparse.Namespace) -> int:
    requested_models = list(dict.fromkeys(str(model) for model in args.models))
    open_discovery_max_steps = int(getattr(args, "open_discovery_max_steps", 12))
    open_study_name = str(getattr(args, "open_study_name", "open-web"))
    if not open_study_name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in open_study_name
    ):
        raise ValueError("--open-study-name must be a lowercase study identifier")
    if not 2 <= len(requested_models) <= 3:
        raise ValueError("generality campaign requires two or three distinct models")
    started_at = datetime.now(UTC)
    output = args.output.expanduser().resolve()
    layout = CampaignLayout.from_root(output)
    layout.prepare()
    contract = _campaign_contract(
        args=args,
        requested_models=requested_models,
        open_discovery_max_steps=open_discovery_max_steps,
        open_study_name=open_study_name,
    )
    _ensure_campaign_contract(layout.manifest_path, contract)
    if bool(getattr(args, "require_new_date", True)):
        duplicate = _completed_campaign_for_date(output, started_at.date().isoformat())
        if duplicate is not None:
            raise RuntimeError(
                f"campaign already completed for this UTC date: {duplicate}; wait for a new date "
                "or pass --no-require-new-date explicitly"
            )
    batch_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
    batch = layout.allocate_batch(now=started_at, batch_id=batch_id)
    batch.prepare()
    logs = batch.logs_dir
    campaign_path = batch.state_path
    campaign_base = {
        "schema_version": 2,
        "batch_id": batch_id,
        "collection_date": started_at.date().isoformat(),
        "provider": args.provider,
        "requested_models": requested_models,
    }
    _write_campaign_state(campaign_path, campaign_base, status="running", component="preflight")
    try:
        probes, preflight_path = _preflight_models(
            provider=args.provider,
            models=requested_models,
            path=batch.endpoint_preflight_path,
            skip=args.skip_endpoint_preflight,
        )
    except Exception as exc:
        _write_campaign_state(
            campaign_path,
            campaign_base,
            status="failed",
            component="preflight",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    available_models = [probe.model for probe in probes if probe.status == "available"]
    if len(available_models) < 2:
        unavailable = [probe.model for probe in probes if probe.status == "unavailable"]
        error = (
            "generality campaign requires at least two available endpoints after preflight; "
            f"unavailable={unavailable}; evidence={preflight_path}"
        )
        _write_campaign_state(
            campaign_path,
            campaign_base,
            status="failed",
            component="preflight",
            error=error,
        )
        raise RuntimeError(error)
    models = _ordered_models(
        available_models,
        started_at.date(),
        str(getattr(args, "model_order", "rotate-by-date")),
    )

    open_root = layout.studies_dir / open_study_name
    _run_component(
        [
            sys.executable,
            "-m",
            "benchmarks.studies.open_web_matrix",
            "--provider",
            args.provider,
            "--models",
            *available_models,
            "--model-order",
            str(getattr(args, "model_order", "rotate-by-date")),
            "--manifest",
            str(args.manifest.resolve()),
            "--output",
            str(open_root),
            "--shards",
            str(args.shards),
            "--max-steps-per-task",
            str(args.open_max_steps),
            "--discovery-max-steps-per-task",
            str(open_discovery_max_steps),
            "--captcha-handling",
            args.captcha_handling,
        ],
        log_path=logs / f"{batch_id}-open-web.log",
        expected_report=None,
        campaign_path=campaign_path,
        campaign_base=campaign_base,
        component="open-web",
    )

    for model in models:
        model_slug = model.replace("/", "-")
        sandbox_output = allocate_execution_dir(
            layout.studies_dir / "sandbox-interaction",
            model=model,
            condition="agent",
            now=started_at,
            execution_id=batch_id,
        )
        _run_component(
            [
                sys.executable,
                "-m",
                "benchmarks.suites.controlled_web.sandbox",
                "--mode",
                "agent",
                "--model",
                model,
                "--report-provider",
                args.provider,
                "--max-steps-per-task",
                str(args.sandbox_max_steps),
                "--output",
                str(sandbox_output),
            ],
            log_path=logs / f"{batch_id}-{model_slug}-sandbox.log",
            expected_report=sandbox_output / "results.json",
            campaign_path=campaign_path,
            campaign_base=campaign_base,
            component=f"{model}:sandbox",
        )
        long_output = allocate_execution_dir(
            layout.studies_dir / "long-horizon",
            model=model,
            condition="agent",
            now=started_at,
            execution_id=batch_id,
        )
        _run_component(
            [
                sys.executable,
                "-m",
                "benchmarks.suites.controlled_web.long_horizon",
                "--mode",
                "agent",
                "--model",
                model,
                "--report-provider",
                args.provider,
                "--max-steps",
                str(args.long_max_steps),
                "--resume-at-step",
                str(args.resume_at_step),
                "--planner-max-tokens",
                str(args.long_planner_max_tokens),
                "--planner-reasoning-effort",
                args.long_planner_reasoning_effort,
                "--output",
                str(long_output),
            ],
            log_path=logs / f"{batch_id}-{model_slug}-long-horizon.log",
            expected_report=long_output / "results.json",
            campaign_path=campaign_path,
            campaign_base=campaign_base,
            component=f"{model}:long-horizon",
        )

    report_paths = _campaign_reports(output)
    portfolio = load_empirical_portfolio(
        report_paths,
        requested_endpoints=[(args.provider, model) for model in requested_models],
        path_root=output,
    )
    snapshot = batch.portfolio_path
    write_empirical_portfolio(portfolio, snapshot)
    latest = layout.latest_portfolio_path
    write_empirical_portfolio(portfolio, latest)
    campaign = {
        "schema_version": 2,
        "status": "completed",
        "finished_at": datetime.now(UTC).isoformat(),
        "batch_id": batch_id,
        "collection_date": started_at.date().isoformat(),
        "provider": args.provider,
        "campaign_contract_path": layout.manifest_path.relative_to(output).as_posix(),
        "requested_models": requested_models,
        "evaluated_models": models,
        "available_models": available_models,
        "execution_model_order": models,
        "model_order_policy": str(getattr(args, "model_order", "rotate-by-date")),
        "excluded_models": [probe.model for probe in probes if probe.status == "unavailable"],
        "endpoint_preflight_path": preflight_path.relative_to(output).as_posix(),
        "report_paths": [path.relative_to(output).as_posix() for path in report_paths],
        "portfolio_path": snapshot.relative_to(output).as_posix(),
        "portfolio_status": portfolio.status,
        "evidence_notice": (
            "This command collects the current UTC date only. Re-run unchanged on two later "
            "dates; no date override exists and same-day repetitions do not create dates."
        ),
    }
    _write_json_atomic(campaign_path, campaign)
    print(
        json.dumps(
            {
                "portfolio_status": portfolio.status,
                "evaluated_models": models,
                "excluded_models": [
                    probe.model for probe in probes if probe.status == "unavailable"
                ],
                "common_complete_dates": portfolio.common_complete_dates,
                "missing_requirements": portfolio.missing_requirements,
                "portfolio": str(snapshot),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if args.require_ready and portfolio.status != "ready" else 0


def _preflight_models(
    *,
    provider: str,
    models: list[str],
    path: Path,
    skip: bool,
) -> tuple[list[EndpointProbe], Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if skip:
        probes = [
            EndpointProbe(
                provider=provider,
                model=model,
                endpoint_host="not-checked",
                status="available",
                checked_at=datetime.now(UTC).isoformat(),
                duration_seconds=0.0,
            )
            for model in models
        ]
        notice = "Endpoint preflight was explicitly skipped; availability was not verified."
    else:
        config = AgentConfig()
        if not config.model_api_url or not config.model_api_key:
            raise RuntimeError(
                "endpoint preflight requires AGENT_MODEL_API_URL and AGENT_MODEL_API_KEY"
            )
        probes = [
            probe_chat_endpoint(
                api_url=config.model_api_url,
                api_key=config.model_api_key,
                provider=provider,
                model=model,
                timeout_seconds=min(float(config.api_timeout), 30.0),
                transient_retries=config.api_transient_retries,
                retry_base_seconds=config.api_retry_base_seconds,
                retry_max_seconds=config.api_retry_max_seconds,
            )
            for model in models
        ]
        notice = (
            "A minimal real inference request checked transport/provider availability. "
            "Unavailable endpoints are excluded from performance metrics, not scored as failures."
        )
    payload = {
        "schema_version": 2,
        "batch_id": path.parent.parent.name,
        "notice": notice,
        "probes": [probe.model_dump(mode="json") for probe in probes],
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return probes, path.resolve()


def _run(command: list[str], *, log_path: Path, expected_report: Path | None) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0 and (expected_report is None or not expected_report.is_file()):
        raise RuntimeError(f"campaign component exited {completed.returncode}; inspect {log_path}")


def _run_component(
    command: list[str],
    *,
    log_path: Path,
    expected_report: Path | None,
    campaign_path: Path,
    campaign_base: dict[str, object],
    component: str,
) -> None:
    _write_campaign_state(campaign_path, campaign_base, status="running", component=component)
    try:
        _run(command, log_path=log_path, expected_report=expected_report)
    except KeyboardInterrupt:
        _write_campaign_state(
            campaign_path,
            campaign_base,
            status="aborted",
            component=component,
            error="KeyboardInterrupt: campaign interrupted by operator",
        )
        raise
    except Exception as exc:
        _write_campaign_state(
            campaign_path,
            campaign_base,
            status="failed",
            component=component,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def _write_campaign_state(
    path: Path,
    base: dict[str, object],
    *,
    status: str,
    component: str,
    error: str | None = None,
) -> None:
    payload = {
        **base,
        "status": status,
        "active_component": component,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if error is not None:
        payload["error"] = error
    _write_json_atomic(path, payload)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _campaign_contract(
    *,
    args: argparse.Namespace,
    requested_models: list[str],
    open_discovery_max_steps: int,
    open_study_name: str,
) -> dict[str, object]:
    """Build the immutable comparison contract shared by every dated batch."""
    manifest = args.manifest.expanduser().resolve()
    source_sha256 = hashlib.sha256(
        (agent_source_fingerprint() + benchmark_source_fingerprint()).encode("ascii")
    ).hexdigest()
    return {
        "format": "webagent-generality-campaign",
        "schema_version": 1,
        "campaign_id": args.output.expanduser().resolve().name,
        "provider": str(args.provider),
        "models": sorted(requested_models),
        "source_sha256": source_sha256,
        "model_order_policy": str(getattr(args, "model_order", "rotate-by-date")),
        "endpoint_preflight_required": not bool(args.skip_endpoint_preflight),
        "captcha_handling": str(args.captcha_handling),
        "components": {
            "open_web": {
                "study_id": open_study_name,
                "task_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "shards": int(args.shards),
                "default_max_steps": int(args.open_max_steps),
                "discovery_max_steps": open_discovery_max_steps,
            },
            "sandbox": {
                "study_id": "sandbox-interaction",
                "max_steps": int(args.sandbox_max_steps),
            },
            "long_horizon": {
                "study_id": "long-horizon",
                "max_steps": int(args.long_max_steps),
                "resume_at_step": int(args.resume_at_step),
                "planner_max_tokens": int(args.long_planner_max_tokens),
                "planner_reasoning_effort": str(args.long_planner_reasoning_effort),
            },
        },
    }


def _ensure_campaign_contract(path: Path, contract: dict[str, object]) -> None:
    """Publish the campaign contract once and reject cross-batch protocol drift."""
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"campaign contract is unreadable: {path}") from exc
        if existing != contract:
            raise RuntimeError(
                "campaign configuration differs from the immutable campaign contract; "
                "use a new --output campaign root"
            )
        return
    _write_json_atomic(path, contract)


def _completed_campaign_for_date(root: Path, collection_date: str) -> Path | None:
    for path in sorted((root / "batches").glob("*/*/batch.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("status") == "completed"
            and payload.get("collection_date") == collection_date
        ):
            return path
    return None


def _campaign_reports(root: Path) -> list[Path]:
    reports: list[Path] = []
    for path in root.glob("studies/**/results.json"):
        relative = path.relative_to(root)
        if "shards" in relative.parts or "evidence" in relative.parts or "runs" in relative.parts:
            continue
        reports.append(path.resolve())
    return sorted(reports)


def main() -> None:
    raise SystemExit(run_campaign(parse_args()))


if __name__ == "__main__":
    main()


__all__ = ["parse_args", "run_campaign"]
