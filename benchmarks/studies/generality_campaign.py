"""Collect one real-date, multi-model generality and long-horizon evidence slice."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.core import default_study_dir, packaged_manifest_path
from webagent.core.config import AgentConfig
from webagent.evaluation.endpoints import EndpointProbe, probe_chat_endpoint
from webagent.evaluation.portfolio import load_empirical_portfolio, write_empirical_portfolio


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_study_dir("generality-campaign-v1"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=packaged_manifest_path("open_web_general.json"),
    )
    parser.add_argument("--shards", type=int, default=3)
    parser.add_argument("--open-max-steps", type=int, default=8)
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
    return parser.parse_args(argv)


def run_campaign(args: argparse.Namespace) -> int:
    requested_models = list(dict.fromkeys(str(model) for model in args.models))
    if not 2 <= len(requested_models) <= 3:
        raise ValueError("generality campaign requires two or three distinct models")
    started_at = datetime.now(UTC)
    output = args.output.expanduser().resolve()
    logs = output / "evidence" / "logs"
    reports_dir = output / "analysis" / "portfolios"
    logs.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    batch_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
    probes, preflight_path = _preflight_models(
        provider=args.provider,
        models=requested_models,
        output=output,
        batch_id=batch_id,
        skip=args.skip_endpoint_preflight,
    )
    models = [probe.model for probe in probes if probe.status == "available"]
    if len(models) < 2:
        unavailable = [probe.model for probe in probes if probe.status == "unavailable"]
        raise RuntimeError(
            "generality campaign requires at least two available endpoints after preflight; "
            f"unavailable={unavailable}; evidence={preflight_path}"
        )

    open_root = output / "suites" / "open-web"
    _run(
        [
            sys.executable,
            "-m",
            "benchmarks.studies.open_web_matrix",
            "--provider",
            args.provider,
            "--models",
            *models,
            "--manifest",
            str(args.manifest.resolve()),
            "--output",
            str(open_root),
            "--shards",
            str(args.shards),
            "--max-steps-per-task",
            str(args.open_max_steps),
            "--captcha-handling",
            args.captcha_handling,
        ],
        log_path=logs / f"{batch_id}-open-web.log",
        expected_report=None,
    )

    for model in models:
        model_slug = model.replace("/", "-")
        sandbox_output = (
            output
            / "suites"
            / "sandbox-interaction"
            / "executions"
            / started_at.date().isoformat()
            / model_slug
            / batch_id
        )
        _run(
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
        )
        long_output = (
            output
            / "suites"
            / "long-horizon"
            / "executions"
            / started_at.date().isoformat()
            / model_slug
            / batch_id
        )
        _run(
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
        )

    report_paths = _campaign_reports(output)
    portfolio = load_empirical_portfolio(report_paths)
    snapshot = reports_dir / f"{batch_id}.json"
    write_empirical_portfolio(portfolio, snapshot)
    latest = reports_dir / "latest.json"
    write_empirical_portfolio(portfolio, latest)
    campaign = {
        "schema_version": 1,
        "batch_id": batch_id,
        "collection_date": started_at.date().isoformat(),
        "provider": args.provider,
        "requested_models": requested_models,
        "evaluated_models": models,
        "excluded_models": [probe.model for probe in probes if probe.status == "unavailable"],
        "endpoint_preflight_path": str(preflight_path),
        "report_paths": [str(path) for path in report_paths],
        "portfolio_path": str(snapshot),
        "portfolio_status": portfolio.status,
        "evidence_notice": (
            "This command collects the current UTC date only. Re-run unchanged on two later "
            "dates; no date override exists and same-day repetitions do not create dates."
        ),
    }
    campaign_path = output / "evidence" / "campaigns" / f"{batch_id}.json"
    campaign_path.parent.mkdir(parents=True, exist_ok=True)
    campaign_path.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding="utf-8")
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
    output: Path,
    batch_id: str,
    skip: bool,
) -> tuple[list[EndpointProbe], Path]:
    path = output / "evidence" / "endpoint-probes" / f"{batch_id}.json"
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
            )
            for model in models
        ]
        notice = (
            "A minimal real inference request checked transport/provider availability. "
            "Unavailable endpoints are excluded from performance metrics, not scored as failures."
        )
    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
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


def _campaign_reports(root: Path) -> list[Path]:
    reports: list[Path] = []
    for path in root.glob("suites/**/results.json"):
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
