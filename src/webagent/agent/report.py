"""Offline, escaped run inspection; execution success is not answer verification."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from webagent.agent.termination import unfinished_result
from webagent.evaluation.trace_verifier import verify_trace


def _safe_path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str):
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) and path.is_file() else None


def _pretty(value: Any, root: Path | None = None) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    if root is not None:
        rendered = rendered.replace(str(root.resolve()), "<RUN_ROOT>")
    return html.escape(rendered)


def _relative_link(root: Path, path: Path, label: str) -> str:
    href = html.escape((Path("..") / path.relative_to(root)).as_posix(), quote=True)
    return f'<a href="{href}">{html.escape(label)}</a>'


def report_verification(root: Path, trace: dict[str, Any]) -> dict[str, Any]:
    """Do not apply a strict-search certificate to ordinary browsing runs."""
    evaluation = trace.get("evaluation") or {}
    if (
        evaluation.get("search_engine_only") is True
        or evaluation.get("mode") == "search_engine_only"
    ):
        return {"applicability": "applicable", **verify_trace(trace, evidence_root=root)}
    return {
        "applicability": "not applicable" if evaluation else "unknown (metadata absent)",
        "valid": None,
        "failures": [],
    }


def _task_judgment(root: Path) -> tuple[Any, Path | None]:
    path = root / "evaluation" / "task.json"
    if not path.exists():
        return "not recorded; execution completion is not independent correctness", None
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return value, path
        # The full evaluator record repeats the final answer in every assertion.
        # Keep this reader compact and link to the immutable source instead.
        compact = {key: item for key, item in value.items() if key != "assertions"}
        compact["assertions"] = [
            {
                "assertion": item.get("assertion"),
                "passed": item.get("passed"),
                "error": item.get("error"),
            }
            for item in value.get("assertions", [])
            if isinstance(item, dict)
        ]
        return compact, path
    except (OSError, ValueError) as exc:
        return {"error": f"Unreadable task judgment: {exc}"}, path


def _observation_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Show what the planner could use without duplicating the raw evidence file."""
    return {
        "schema_version": data.get("schema_version"),
        "observation_id": data.get("observation_id"),
        "phase": data.get("phase"),
        "step_number": data.get("step_number"),
        "timestamp": data.get("timestamp"),
        "url": data.get("url"),
        "title": data.get("title"),
        "dom_sha256": data.get("dom_sha256"),
        "metadata": data.get("metadata"),
        "screenshot": data.get("screenshot"),
        "full_page_screenshot": data.get("full_page_screenshot"),
        "selected_context": {
            "viewport": data.get("viewport_context"),
            "document_supplement": data.get("document_context"),
        },
        "collected_counts": {
            "elements": len(data.get("elements", [])),
            "viewport_blocks": len(data.get("viewport_blocks", [])),
            "document_blocks": len(data.get("document_blocks", [])),
        },
    }


def _observation(root: Path, value: Any, phase: str) -> str:
    path = _safe_path(root, value)
    if path is None:
        return f"<div><h3>{phase}</h3><p>Not recorded / action not attempted. See lifecycle events.</p></div>"
    data = json.loads(path.read_text())
    metadata = data.get("metadata", {})
    status = metadata.get("status", "legacy / unknown")
    shot = data.get("screenshot") or {}
    image = _safe_path(root, shot.get("path"))
    picture = (
        f'<img loading="lazy" width="{html.escape(str(shot.get("width", 1280)), quote=True)}" height="{html.escape(str(shot.get("height", 720)), quote=True)}" alt="{phase} viewport screenshot" src="../{html.escape(image.relative_to(root).as_posix(), quote=True)}">'
        if image
        else "<p>No screenshot captured; this is not a blank-page screenshot.</p>"
    )
    return (
        f"<div><h3>{phase}: {html.escape(status)}</h3>{picture}"
        f"<p>{_relative_link(root, path, 'Open complete observation JSON')}</p>"
        f"<details><summary>Planner-visible DOM context / capture status</summary>"
        f"<pre>{_pretty(_observation_summary(data), root)}</pre></details></div>"
    )


def _step_summary(step: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "step_number",
        "tool",
        "parameters",
        "success",
        "outcome",
        "error",
        "planner_visible_result",
        "observations",
        "result_ref",
        "lifecycle",
    )
    summary = {key: step[key] for key in keys if key in step}
    policy = step.get("policy")
    if isinstance(policy, dict):
        ledger = policy.get("candidate_ledger")
        summary["policy"] = {
            key: policy.get(key)
            for key in (
                "name",
                "decision",
                "selected_candidate_url",
                "selected_candidate_identity_endorsed",
                "version_frontier",
                "newer_version_leads_resolved",
            )
            if key in policy
        }
        if isinstance(ledger, dict):
            summary["policy"]["candidate_ledger"] = {
                key: ledger.get(key)
                for key in ("status", "missing", "unverified_release_leads")
                if key in ledger
            }
    return summary


def _planner_attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "step_number",
        "attempt",
        "timestamp",
        "duration_seconds",
        "finish_reason",
        "error",
        "tokens",
        "observation_path",
        "observation_input",
    )
    return {key: item[key] for key in keys if key in item}


def _step(root: Path, step: dict[str, Any], attempts: list[dict[str, Any]]) -> str:
    number = step["step_number"]
    observations = step.get("observations", {})
    raw = _safe_path(root, step.get("result_ref", {}).get("path"))
    inputs = [item for item in attempts if item.get("step_number") == number]
    details = {
        "action": _step_summary(step),
        "planner_attempts": [_planner_attempt_summary(item) for item in inputs],
    }
    delivery = [
        "sent"
        if item.get("observation_input", {}).get("screenshot_sent")
        else str(
            item.get("observation_input", {}).get("screenshot_omission_reason")
            or "not sent / unknown"
        )
        for item in inputs
    ]
    outcome = step.get("outcome") or ("OK" if step.get("success") else "FAILED")
    return (
        f'<section id="step-{number}"><h2>Step {number} · {html.escape(step["tool"])} · '
        f'{html.escape(outcome)}</h2><p>Planner image per attempt: {html.escape(", ".join(delivery) or "unknown")}</p><div class="pair">'
        + _observation(root, observations.get("pre"), "pre")
        + _observation(root, observations.get("post"), "post")
        + "</div>"
        + (
            f"<p>{_relative_link(root, raw, 'Open complete redacted tool result')}</p>"
            if raw
            else ""
        )
        + f"<details><summary>Action, planner input metadata and policy summary</summary>"
        f"<pre>{_pretty(details, root)}</pre></details></section>"
    )


def _observed_events(root: Path) -> list[dict[str, Any]]:
    path = root / "trajectory" / "events.jsonl"
    events: dict[int, dict[str, Any]] = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            event = json.loads(line)
        except ValueError:
            continue  # Raw lifecycle panel retains a partially written trailing record.
        if not isinstance(event, dict) or not isinstance(event.get("step"), int):
            continue
        if event.get("type") not in {"observed", "action_started", "action_finished"}:
            continue
        entry = events.setdefault(event["step"], {"step_number": event["step"]})
        entry.update(event)
    return list(events.values())


def _timeline_steps(root: Path, trace: dict[str, Any]) -> list[dict[str, Any]]:
    steps = {step["step_number"]: step for step in trace["steps"]}
    for attempt in [*_observed_events(root), *trace.get("planner_attempts", [])]:
        number = attempt["step_number"]
        if number not in steps:
            pre = attempt.get("observation_path")
            observations = (
                {"pre": pre, "post": str(Path(pre).with_name("post.json"))} if pre else {}
            )
            steps[number] = {
                "step_number": number,
                "tool": (
                    f"{attempt['tool']} / lifecycle only; post or step record incomplete"
                    if attempt.get("tool")
                    else "planning / no executed action"
                ),
                "success": attempt.get("success", False),
                "outcome": (
                    None
                    if "success" in attempt
                    else "OUTCOME UNKNOWN"
                    if attempt.get("tool")
                    else "NOT EXECUTED"
                ),
                "observations": observations,
                "lifecycle": attempt,
            }
    return [steps[number] for number in sorted(steps)]


def _merged_lifecycle(raw: str, trace: dict[str, Any]) -> list[dict[str, Any]]:
    events = list(trace.get("events", []))
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    unique = {json.dumps(event, sort_keys=True): event for event in events}
    return sorted(unique.values(), key=lambda event: str(event.get("timestamp", "")))


def write_report(root: Path, trace: dict[str, Any]) -> Path:
    attempts = trace.get("planner_attempts", [])
    sent = sum(bool(a.get("observation_input", {}).get("screenshot_sent")) for a in attempts)
    certificate_path = root / "trajectory" / "verification.json"
    certificate = (
        json.loads(certificate_path.read_text()) if certificate_path.exists() else {"valid": None}
    )
    terminal: dict[str, Any] = next(
        (
            s["policy"]
            for s in reversed(trace["steps"])
            if s.get("policy", {}).get("candidate_ledger")
        ),
        {},
    )
    verification = report_verification(root, trace)
    overview = {
        "execution_status": trace["status"],
        "trajectory_compliance": verification.get("valid"),
        "strict_search_verification_applicability": verification["applicability"],
        "verification_failures": verification.get("failures", []),
        "stored_certificate_valid": certificate.get("valid"),
        "answer_correctness": "not independently evaluated",
        "latest_evidence": terminal.get("candidate_ledger", {}).get("status", "not recorded"),
        "planning_screenshots_sent": sent,
        "planning_requests": len(attempts),
    }
    body = "".join(_step(root, step, attempts) for step in _timeline_steps(root, trace))
    events_path = root / "trajectory" / "events.jsonl"
    lifecycle = events_path.read_text() if events_path.exists() else "Not recorded"
    final_result = trace.get("final_result")
    if not final_result and trace["status"] != "completed":
        final_result = unfinished_result(trace["status"], terminal, trace.get("events", []))
        final_result["reconstructed_from_trace"] = True
    judgment, judgment_path = _task_judgment(root)
    source_links = [
        _relative_link(root, root / "trajectory" / "trace.json", "trace.json"),
    ]
    if events_path.exists():
        source_links.append(_relative_link(root, events_path, "events.jsonl"))
    if certificate_path.exists():
        source_links.append(_relative_link(root, certificate_path, "verification.json"))
    if judgment_path is not None:
        source_links.append(_relative_link(root, judgment_path, "evaluation/task.json"))
    content = (
        "<!doctype html><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'\">"
        "<title>Run evidence report</title><style>body{font:15px system-ui;max-width:1500px;margin:32px auto;padding:0 20px;background:#f5f6fa;color:#172033}section{background:white;padding:20px;margin:20px 0;border-radius:12px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}.pair>div{min-width:0}img{width:100%;height:auto;border:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;padding:10px}h1{font-size:26px}@media(max-width:800px){.pair{grid-template-columns:1fr}}</style>"
        f"<h1>{html.escape(trace['task'])}</h1><p>Saved screenshots are audit evidence, not proof the model received them.</p>"
        f"<nav>Complete source evidence: {' · '.join(source_links)}</nav>"
        f"<pre>{_pretty(overview, root)}</pre><details open><summary>Final answer / incomplete handoff (unverified)</summary><pre>{_pretty(final_result, root)}</pre></details>"
        f"<details><summary>Independent task assertions (not unrestricted semantic grading)</summary><pre>{_pretty(judgment, root)}</pre></details>"
        f"<details><summary>Latest candidates and date evidence</summary><pre>{_pretty(terminal.get('candidate_ledger'), root)}</pre></details>"
        f"<details><summary>Unified lifecycle: actions, challenges, strategy switches and termination</summary><pre>{_pretty(_merged_lifecycle(lifecycle, trace), root)}</pre></details>"
        f"<p>The raw action journal is linked above, including any partial trailing record.</p>{body}"
    )
    path = root / "report" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".html.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return path
